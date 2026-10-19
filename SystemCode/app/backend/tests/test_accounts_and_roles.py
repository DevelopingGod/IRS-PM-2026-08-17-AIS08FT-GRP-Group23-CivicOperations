"""Accounts, roles and the tech team's switches, through the real HTTP API."""
import os

import pytest

os.environ.setdefault("CIVICOPS_WARMUP", "0")

from fastapi.testclient import TestClient  # noqa: E402

from app.backend import accounts, addresses, db, main, service, switches  # noqa: E402


@pytest.fixture()
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "t.db")
    monkeypatch.setattr(service, "MEASURE_FILE", tmp_path / "m.json")
    with TestClient(main.app) as c:
        yield c


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def test_resident_registers_with_email_only_and_signs_in_with_the_id(api):
    r = api.post("/api/auth/register", json={"email": "Priya@Example.org"}).json()
    assert r["role"] == "resident" and r["user_id"].startswith("NYC-")
    again = api.post("/api/auth/register", json={"email": "priya@example.org"})
    assert again.status_code == 400 and "already has a user ID" in again.json()["detail"]
    assert r["user_id"] not in again.text                          # the existing ID is never revealed
    login = api.post("/api/auth/login", json={"user_id": r["user_id"].lower()}).json()
    assert api.get("/api/auth/me", headers=bearer(login["token"])).json()["role"] == "resident"
    with db.conn() as c:
        assert "priya" not in str([tuple(x) for x in c.execute("select * from users")])


def test_bad_email_and_unknown_id_are_refused(api):
    assert api.post("/api/auth/register", json={"email": "not-an-email"}).status_code == 400
    assert api.post("/api/auth/login", json={"user_id": "NYC-AAAA-AAAA"}).status_code == 401


def test_staff_need_a_pin_and_five_wrong_pins_lock_the_id(api):
    u = accounts.create_user("staff", "Dispatcher", "test")
    assert api.post("/api/auth/login", json={"user_id": u["user_id"]}).status_code == 401
    ok = api.post("/api/auth/login", json={"user_id": u["user_id"], "pin": u["pin"]})
    assert ok.status_code == 200 and ok.json()["role"] == "staff"
    wrong = "000000" if u["pin"] != "000000" else "111111"
    for _ in range(5):
        api.post("/api/auth/login", json={"user_id": u["user_id"], "pin": wrong})
    locked = api.post("/api/auth/login", json={"user_id": u["user_id"], "pin": u["pin"]})
    assert locked.status_code == 401 and "locked" in locked.json()["detail"]


def test_each_role_sees_only_its_own_portal(api):
    res = api.post("/api/auth/register", json={"email": "r@example.org"}).json()["token"]
    s = accounts.create_user("staff", "S", "test")
    t = accounts.create_user("tech", "T", "test")
    st = api.post("/api/auth/login", json={"user_id": s["user_id"], "pin": s["pin"]}).json()["token"]
    tt = api.post("/api/auth/login", json={"user_id": t["user_id"], "pin": t["pin"]}).json()["token"]
    assert api.get("/api/staff/queue").status_code == 401
    assert api.get("/api/staff/queue", headers=bearer(res)).status_code == 403
    assert api.get("/api/tech/health", headers=bearer(res)).status_code == 403
    assert api.get("/api/crew/jobs", headers=bearer(st)).status_code == 403
    assert api.get("/api/tech/health", headers=bearer(st)).status_code == 403
    assert api.get("/api/staff/queue", headers=bearer(st)).status_code == 200
    assert api.get("/api/tech/health", headers=bearer(tt)).status_code == 200
    assert api.post("/api/report", headers=bearer(st), json={"place": {}}).status_code == 403


def test_a_switch_reaches_every_part_of_the_app_and_safety_cannot_be_switched_off(api):
    t = accounts.create_user("tech", "T", "test")
    tt = api.post("/api/auth/login", json={"user_id": t["user_id"], "pin": t["pin"]}).json()["token"]
    off = api.put("/api/tech/components", headers=bearer(tt), json={"name": "Intake bot + Emergency check", "enabled": False})
    assert off.status_code == 400 and "never be switched off" in off.json()["detail"]
    assert api.put("/api/tech/components", headers=bearer(tt), json={"name": "Admin questions", "enabled": False}).status_code == 200
    s = accounts.create_user("staff", "S", "test")
    st = api.post("/api/auth/login", json={"user_id": s["user_id"], "pin": s["pin"]}).json()["token"]
    assert api.post("/api/staff/ask", headers=bearer(st), json={"question": "How many open requests?"}).json()["outcome"] == "OFF"
    comps = {c["name"]: c for c in api.get("/api/tech/health", headers=bearer(tt)).json()["components"]}
    assert comps["Admin questions"]["enabled"] is False and comps["Intake bot + Emergency check"]["locked"]


def test_a_crew_closes_only_its_own_jobs(api):
    crews = list(main.data.crews())
    if len(crews) < 2:
        pytest.skip("data/processed is not present on this machine")
    a = accounts.create_user("crew", "A", "test", crews[0])
    at = api.post("/api/auth/login", json={"user_id": a["user_id"], "pin": a["pin"]}).json()["token"]
    with db.conn() as c:
        c.execute("insert into requests values (?,?,?,?)", ("CO-TEST01", "2026-10-18T09:00:00-04:00", "ASSIGNED",
                                                            '{"created_at": "2026-10-18T09:00:00-04:00", "slots": {}}'))
        c.execute("insert into crew_jobs values (?,?,?,?,?)", ("CO-TEST01", crews[1], "2026-10-18T09:05:00-04:00", None, None))
    r = api.post("/api/crew/jobs/CO-TEST01/close", headers=bearer(at), json={"outcome": "VALID_RESOLVED"})
    assert r.status_code == 403


def test_addresses_written_differently_match():
    assert addresses.normalise("450 Atlantic Ave.") == addresses.normalise("450 ATLANTIC AVENUE")
    assert addresses.normalise("37-61 79th St") == addresses.normalise("37-61 79 STREET")


def test_switches_module_refuses_unknown_names(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB", tmp_path / "s.db")
    db.init()
    switches.init()
    with pytest.raises(ValueError):
        switches.set_switch("Not a component", False, "test", {"Admin questions"})
