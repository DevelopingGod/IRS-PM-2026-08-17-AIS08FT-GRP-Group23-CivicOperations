"""Crew deployment and route optimisation over a real backlog, through the HTTP API: routes go to
the crews automatically, the crew works its route in that order, staff can pull a job back or give
it to another crew, and switching the component off falls back to hand assignment."""
import json
import os

import pytest

os.environ.setdefault("CIVICOPS_WARMUP", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.backend import accounts, backlog, db, dispatch, main, service, switches  # noqa: E402
from app.backend.plugins.registry import SWITCHABLE  # noqa: E402


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("crew")
    mp = pytest.MonkeyPatch()
    mp.setattr(db, "DB", tmp / "t.db")
    mp.setattr(service, "MEASURE_FILE", tmp / "m.json")
    dispatch.reset()
    with TestClient(main.app) as c:
        backlog.import_backlog(count=40)
        s = accounts.create_user("staff", "Dispatcher", "test")
        token = c.post("/api/auth/login", json={"user_id": s["user_id"], "pin": s["pin"]}).json()["token"]
        yield c, {"Authorization": f"Bearer {token}"}
    dispatch.reset()
    mp.undo()


def _open_jobs():
    with db.conn() as c:
        return [dict(r) for r in c.execute("select request_id, crew_id, assigned_at from crew_jobs where outcome is null order by assigned_at")]


def test_routes_go_to_the_crews_automatically(world):
    api, staff = world
    r = api.get("/api/staff/plan?wait=30", headers=staff).json()
    assert r["outcome"] == "LIVE"
    jobs = _open_jobs()
    assert jobs                                                  # assigned without anyone approving
    queue = {q["request_id"]: q for q in api.get("/api/staff/queue", headers=staff).json()["items"]}
    for j in jobs:
        assert queue[j["request_id"]]["queue_status"] == "provisional"   # held or review requests are never sent
    plan = api.get("/api/staff/plan?wait=30", headers=staff).json()["plan"]
    crew = next(c for c in plan["crews"] if c["approved"])
    assert crew["depot"]["lat"] and crew["position"]["lat"]      # the start and where it is, for the map
    assert crew["roads"]


def test_the_crew_works_its_route_in_the_planned_order(world):
    jobs = _open_jobs()
    crew_id = max({j["crew_id"] for j in jobs}, key=lambda c: sum(j["crew_id"] == c for j in jobs))
    mine = service.crew_jobs(crew_id)
    assert [j["request_id"] for j in mine["jobs"]] == [j["request_id"] for j in jobs if j["crew_id"] == crew_id]
    assert "assigned automatically" in mine["order_source"]
    assert mine["depot"]["lat"] is not None and mine["position"]["lat"] is not None


def test_pulling_a_job_back_keeps_it_from_automatic_assignment(world):
    api, staff = world
    job = _open_jobs()[0]
    r = api.post(f"/api/staff/requests/{job['request_id']}/unassign", headers=staff)
    assert r.status_code == 200
    d = api.get(f"/api/staff/requests/{job['request_id']}", headers=staff).json()
    assert d["status"] == "OPEN" and d["dispatch_hold"] is True and d["crew_job"] is None
    api.get("/api/staff/plan?wait=30", headers=staff)
    api.get("/api/staff/plan?wait=30", headers=staff)
    assert job["request_id"] not in {j["request_id"] for j in _open_jobs()}   # not given back by itself
    other = next(c for c in api.get("/api/staff/crews", headers=staff).json() if c["crew_id"] != job["crew_id"])
    ok = api.post(f"/api/staff/requests/{job['request_id']}/assign", json={"crew_id": other["crew_id"]}, headers=staff)
    assert ok.status_code == 200
    d = api.get(f"/api/staff/requests/{job['request_id']}", headers=staff).json()
    assert d["crew_job"]["crew_id"] == other["crew_id"] and not d.get("dispatch_hold")
    assert api.post("/api/staff/requests/CO-NOPE00/unassign", headers=staff).status_code == 400


def test_switched_off_nothing_is_assigned_automatically(world):
    api, staff = world
    switches.set_switch(dispatch.NAME, False, "test", set(SWITCHABLE))
    try:
        before = len(_open_jobs())
        r = api.get("/api/staff/plan?wait=5", headers=staff).json()
        assert r["outcome"] == "OFF" and r["plan"] is None
        assert all(q["proposed"] is None for q in api.get("/api/staff/queue", headers=staff).json()["items"])
        assert len(_open_jobs()) == before
    finally:
        switches.set_switch(dispatch.NAME, True, "test", set(SWITCHABLE))


def test_real_streets_for_a_zoomed_map(world):
    api, staff = world
    r = api.get("/api/map/streets?south=40.66&west=-73.99&north=40.70&east=-73.94", headers=staff)
    assert r.status_code == 200
    streets = r.json()
    assert len(streets) > 100 and {"k", "p"} <= set(streets[0]) and len(streets[0]["p"]) >= 2
    assert api.get("/api/map/streets?south=40.4&west=-74.3&north=40.95&east=-73.6", headers=staff).status_code == 400


def test_each_request_opens_its_own_incident_group(world):
    """Regression: requests used to reach Duplicate detection without an ID, so every incident
    shared the group DG-NEW and one duplicate's second reporter counted for all of them."""
    with db.conn() as c:
        rows = [json.loads(r["data"]) for r in c.execute("select data from requests")]
    groups = [q.get("duplicate_group_id") for q in rows if q.get("duplicate_relation") == "NEW"]
    assert groups and "DG-NEW" not in groups and len(set(groups)) == len(groups)


def test_a_confirmed_impact_on_a_school_moves_the_request_up(world):
    api, staff = world
    items = api.get("/api/staff/queue", headers=staff).json()["items"]
    q = next((x for x in items if x["sensitive_site_type"] and not x["on_hold"]), None)
    if q is None:          # no school or hospital in this small backlog: mark one as External factors would
        q = next(x for x in items if not x["on_hold"])
        doc = db.load_json("requests", q["request_id"])
        doc["sensitive_site_type"] = "school"
        db.save_json("requests", q["request_id"], doc, created_at=doc["created_at"], status="OPEN")
    before = next(x for x in api.get("/api/staff/queue", headers=staff).json()["items"] if x["request_id"] == q["request_id"])
    r = api.post(f"/api/staff/requests/{q['request_id']}/confirm-impact", headers=staff)
    assert r.status_code == 200 and r.json()["sensitive_impact_verified"] is True
    after = next(x for x in api.get("/api/staff/queue", headers=staff).json()["items"] if x["request_id"] == q["request_id"])
    assert after["priority_score"] > before["priority_score"]
    assert any("sensitive" in reason.lower() for reason in after["priority_reasons"])
    assert after["queue_position"] <= before["queue_position"]


def test_prioritisation_finds_official_sla_targets(world):
    """Khalid's SLA table reaches the ranking through the mapping in data/reference/sla. Every
    request type has a target: the official NYC SLA, or a labelled team target where NYC has none."""
    api, staff = world
    items = [q for q in api.get("/api/staff/queue", headers=staff).json()["items"] if not q.get("on_hold")]
    official = [q for q in items if any(r.startswith("Official NYC SLA") for r in q["priority_reasons"])]
    team = [q for q in items if any(r.startswith("Team SLA target") for r in q["priority_reasons"])]
    assert len(official) >= len(items) * 0.6
    assert len(official) + len(team) == len(items)


def test_a_linked_duplicate_follows_its_original_to_closure(world):
    """Regression: a second report of the same pothole was held as a duplicate and never moved on,
    so its reporter saw no crew and no closure. Now it shows the original's crew and closes with it."""
    api, staff = world
    text = "there is a deep pothole on atlantic avenue near 4th avenue in brooklyn, a cyclist nearly fell"
    tokens, ids = [], []
    for n in range(2):
        t = api.post("/api/auth/register", json={"email": f"dup.{n}@example.org"}).json()["token"]
        r = api.post("/api/chat", json={"text": text}, headers={"Authorization": f"Bearer {t}"}).json()
        assert r["state"] == "FILED"
        tokens.append({"Authorization": f"Bearer {t}"}); ids.append(r["request_id"])
    first, second = ids
    dup = api.get(f"/api/staff/requests/{second}", headers=staff).json()
    assert dup["duplicate_relation"] == "DUPLICATE" and dup["duplicate_of"] == first
    crew = next(c for c in api.get("/api/staff/crews?agency=DOT&borough=BROOKLYN", headers=staff).json())
    assert api.post(f"/api/staff/requests/{first}/assign", json={"crew_id": crew["crew_id"]}, headers=staff).status_code == 200
    assert api.get(f"/api/me/requests/{second}", headers=tokens[1]).json()["crew_assigned"] is True
    service.close_job(first, "VALID_RESOLVED", crew["crew_id"])
    mine = api.get(f"/api/me/requests/{second}", headers=tokens[1]).json()
    assert mine["status"] == "CLOSED" and mine["outcome"] == "VALID_RESOLVED"
    assert [r["request_id"] for r in api.get("/api/me/requests", headers=tokens[1]).json()] == [second]
