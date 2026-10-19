"""Crew deployment and route optimisation over a real backlog, through the HTTP API: a proposal
only, approval by city staff locks a route, the crew works it in that order, and switching the
component off falls back to hand assignment."""
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


def test_the_plan_is_a_proposal_over_plannable_requests_only(world):
    api, staff = world
    r = api.get("/api/staff/plan?wait=15", headers=staff).json()
    assert r["outcome"] == "LIVE" and not r["planning"]
    plan = r["plan"]
    assert plan["proposal_only"] is True
    assert plan["planned"] > 0 and plan["crews"]
    queue = {q["request_id"]: q for q in api.get("/api/staff/queue", headers=staff).json()["items"]}
    for rid in plan["proposed"]:
        assert queue[rid]["queue_status"] == "provisional"       # review and held requests are never planned
    stop = plan["crews"][0]["stops"][0]
    assert stop["est_start"] and stop["travel_min"] >= 0 and stop["label"]
    assert plan["crews"][0]["roads"]                             # the road it drives, for the map
    with db.conn() as c:
        assert c.execute("select count(*) n from crew_jobs").fetchone()["n"] == 0   # nothing assigned by itself


def test_approving_a_route_locks_it_and_the_crew_works_it_in_that_order(world):
    api, staff = world
    plan = api.get("/api/staff/plan?wait=15", headers=staff).json()["plan"]
    route = max(plan["crews"], key=lambda c: len(c["stops"]))
    ids = [s["request_id"] for s in route["stops"]]
    ok = api.post("/api/staff/plan/approve", json={"crew_id": route["crew_id"], "request_ids": ids}, headers=staff)
    assert ok.status_code == 200 and ok.json()["approved"] == len(ids)
    jobs = service.crew_jobs(route["crew_id"])
    assert [j["request_id"] for j in jobs["jobs"]] == ids
    assert "Crew deployment" in jobs["order_source"]
    after = api.get("/api/staff/plan?wait=15", headers=staff).json()["plan"]
    assert not set(ids) & set(after["proposed"])                 # locked jobs are never planned again
    wrong = api.post("/api/staff/plan/approve", json={"crew_id": "CRW-DOT-001", "request_ids": ids[:1]}, headers=staff)
    assert wrong.status_code == 400


def test_switched_off_it_falls_back_to_hand_assignment(world):
    api, staff = world
    switches.set_switch(dispatch.NAME, False, "test", set(SWITCHABLE))
    try:
        r = api.get("/api/staff/plan", headers=staff).json()
        assert r["outcome"] == "OFF" and r["plan"] is None
        assert all(q["proposed"] is None for q in api.get("/api/staff/queue", headers=staff).json()["items"])
    finally:
        switches.set_switch(dispatch.NAME, True, "test", set(SWITCHABLE))


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


def test_approve_all_routes_sends_every_proposed_stop(world):
    api, staff = world
    plan = api.get("/api/staff/plan?wait=15", headers=staff).json()["plan"]
    expected = sum(len(c["stops"]) for c in plan["crews"])
    r = api.post("/api/staff/plan/approve-all", headers=staff)
    assert r.status_code == 200 and r.json()["approved"] == expected
    with db.conn() as c:
        assert c.execute("select count(*) n from crew_jobs where outcome is null").fetchone()["n"] >= expected
    after = api.get("/api/staff/plan?wait=15", headers=staff).json()["plan"]
    assert after["planned"] == 0 or not set(after["proposed"]) & {s["request_id"] for c in plan["crews"] for s in c["stops"]}


def test_prioritisation_finds_official_sla_targets(world):
    """Khalid's SLA table reaches the ranking through the mapping in data/reference/sla."""
    api, staff = world
    items = api.get("/api/staff/queue", headers=staff).json()["items"]
    with_sla = [q for q in items if any(r.startswith("Official NYC SLA") for r in q["priority_reasons"])]
    assert len(with_sla) >= len(items) * 0.6
