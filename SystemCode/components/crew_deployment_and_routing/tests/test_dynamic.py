import copy
import json

import pytest

from crew_dispatch.dynamic.controller import DispatchController
from crew_dispatch.dynamic.simulator import ReplaySimulator
from crew_dispatch.fixtures import c11_record, crew_record, node_latlon, t
from crew_dispatch.outputs import write_new
from crew_dispatch.schemas import Crew, DispatchState, LOCKED_STATES
from crew_dispatch.upstream.c11_provider import VendoredC11Provider


def mk_queue(net, specs):
    """specs: list of (rid, row, col, status). Positions follow list order (C11 global order)."""
    out, pos = [], 0
    for rid, r, c, st in specs:
        if st != "hold":
            pos += 1
        out.append(c11_record(rid, "DOT", "ROAD_REPAIR", *node_latlon(net, r, c), pos if st != "hold" else None, status=st, service=30))
    act = [x for x in out if x["c11_queue_status"] != "hold"]
    return act + [x for x in out if x["c11_queue_status"] == "hold"]


def ev(kind, time, **kw):
    return {"type": kind, "time": time, "source": "test", "simulated": True, **kw}


@pytest.fixture()
def ctl(net, router, cfg, catalog, tmp_path):
    crews = [Crew.from_record(crew_record("C1", "DOT", ["ROAD_REPAIR"], *node_latlon(net, 0, 0), budget=300)),
             Crew.from_record(crew_record("C2", "DOT", ["ROAD_REPAIR"], *node_latlon(net, 7, 7), budget=300))]
    c = DispatchController(crews, router, cfg, catalog, tmp_path / "run", run_id="test", seed=1, maps=False)
    c.start_duty(t(9))
    return c


BASE = [("A", 1, 1, "provisional"), ("B", 2, 5, "provisional"), ("C", 6, 6, "provisional"), ("D", 5, 2, "provisional"),
        ("R", 3, 3, "provisional_review"), ("H", 4, 4, "hold")]


def test_initial_plan_published_and_versioned(ctl, net):
    res = ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    assert res.published and res.plan_version == 1
    assert (ctl.run_dir / "plans" / "plan_v0001.json").exists()
    doc = json.loads((ctl.run_dir / "plans" / "plan_v0001.json").read_text())
    assert doc["authorization"]["dispatch_authorized"] is False
    assert doc["pending_review"] == ["R"] and doc["held"] == ["H"]
    assert ctl.job_states["R"] == DispatchState.PENDING_REVIEW and ctl.job_states["H"] == DispatchState.HELD
    assert set(ctl.plan.assignment()) == {"A", "B", "C", "D"}
    # upstream record preserved verbatim under "request"
    rec = next(r for r in doc["requests"] if r["request"]["request_id"] == "A")["request"]
    assert rec["priority_band"] == "PLANNED" and "dispatch" not in rec
    with pytest.raises(FileExistsError):
        write_new(ctl.run_dir / "plans" / "plan_v0001.json", "{}")


def test_no_material_change_no_new_version(ctl, net):
    q = mk_queue(net, BASE)
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=q, trigger="new_request"))
    r1 = ctl.handle(ev("QUEUE_UPDATED", t(9, 5), queue=copy.deepcopy(q), trigger="periodic"))
    r2 = ctl.handle(ev("TICK", t(9, 20)))
    assert not r1.published and not r2.replanned
    assert ctl.version == 1


def test_new_request_and_rank_change_trigger_replan(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    # a new request arrives and C11 ranks it first: every other position shifts
    new = [("N", 7, 0, "provisional")] + BASE
    res = ctl.handle(ev("QUEUE_UPDATED", t(9, 3), queue=mk_queue(net, new), trigger="new_request"))
    assert res.replanned and res.forced
    assert "N" in ctl.plan.assignment()
    assert res.published and res.changes["newly_assigned"] == ["N"]
    log = [a for a in ctl.audit if a["event_type"] == "QUEUE_UPDATED"]
    assert log[-1]["published"] and "rank change" in log[-1]["reason"]


def run_job(ctl, rid, cid, t_approve):
    assert ctl.handle(ev("APPROVAL", t_approve, request_id=rid, crew_id=cid, verified=True, approver="staff-1")).reason.startswith(rid)
    ctl.handle(ev("JOB_STATUS", t_approve, request_id=rid, crew_id=cid, state="EN_ROUTE", est_travel_min=5))


def test_locked_jobs_never_replanned(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    first = {cid: st[0]["request_id"] for cid, st in ctl.plan.routes.items() if st}
    for cid, rid in first.items():
        run_job(ctl, rid, cid, t(9, 1))
    ctl.handle(ev("JOB_STATUS", t(9, 6), request_id=first["C1"], crew_id="C1", state="IN_PROGRESS"))
    new = [("N", 7, 0, "provisional"), ("M", 0, 7, "provisional")] + BASE
    ctl.handle(ev("QUEUE_UPDATED", t(9, 8), queue=mk_queue(net, new), trigger="new_request"))
    for cid, stops in ctl.plan.routes.items():
        for s in stops:
            assert ctl.job_states[s["request_id"]] not in LOCKED_STATES
    assert ctl.job_states[first["C1"]] == DispatchState.IN_PROGRESS
    assert ctl.job_states[first["C2"]] == DispatchState.EN_ROUTE
    assert ctl.locked_violations == 0
    # the crew that is working plans from the job site, after the job
    cp = ctl.plan.crews["C1"]
    assert cp.available_at >= t(9, 36) and cp.locked[0].request_id == first["C1"]


def test_en_route_requires_verified_approval(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    rid = ctl.plan.routes["C1"][0]["request_id"]
    res = ctl.handle(ev("APPROVAL", t(9, 1), request_id=rid, crew_id="C1", verified=False, approver="?"))
    assert res.reason.startswith("rejected") and ctl.job_states[rid] == DispatchState.PLANNED
    with pytest.raises(ValueError):
        ctl.handle(ev("JOB_STATUS", t(9, 1), request_id=rid, crew_id="C1", state="EN_ROUTE"))


def test_completion_only_via_status_event(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    rid = ctl.plan.routes["C1"][0]["request_id"]
    run_job(ctl, rid, "C1", t(9, 1))
    ctl.handle(ev("JOB_STATUS", t(9, 5), request_id=rid, crew_id="C1", state="IN_PROGRESS"))
    # estimated finish (09:35) passes without confirmation -> still IN_PROGRESS, flagged awaiting update
    ctl.handle(ev("TICK", t(11)))
    assert ctl.job_states[rid] == DispatchState.IN_PROGRESS
    assert [o["request_id"] for o in ctl.awaiting_status_updates(t(11))] == [rid]
    assert "awaiting Crew Portal status update" in ctl.audit[-1]["reason"]
    plan = ctl.snapshot(t(11))
    from crew_dispatch.engine import crew_planning_state
    cp = crew_planning_state(ctl.crews["C1"], ctl.crew_status["C1"], t(11), ctl.cfg, ctl.router)
    assert cp.awaiting_update == [rid] and cp.available_at == t(11, 15)
    with pytest.raises(ValueError):   # completion for a crew that does not hold the job
        ctl.handle(ev("JOB_STATUS", t(11, 2), request_id=rid, crew_id="C2", state="COMPLETED"))
    ctl.handle(ev("JOB_STATUS", t(11, 3), request_id=rid, crew_id="C1", state="COMPLETED", actual_travel_min=4, actual_service_min=40))
    assert ctl.job_states[rid] == DispatchState.COMPLETED
    assert ctl.crew_status["C1"].worked_min == pytest.approx(44)
    # completed work is never re-planned even if C11 still lists it
    assert rid not in ctl.plan.assignment()


def test_crew_breakdown_forces_replan(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    assert ctl.plan.routes["C2"]
    res = ctl.handle(ev("CREW_STATUS", t(9, 10), crew_id="C2", available=False, note="breakdown"))
    assert res.forced and res.published
    assert not ctl.plan.routes.get("C2")
    assert ctl.plan.crews["C2"].reason == "crew_unavailable"


def test_review_completion_makes_request_eligible(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    assert "R" not in ctl.plan.assignment()
    reviewed = [(rid, r, c, "provisional" if rid == "R" else st) for rid, r, c, st in BASE]
    ctl.handle(ev("QUEUE_UPDATED", t(9, 30), queue=mk_queue(net, reviewed), trigger="review_completed"))
    ctl.handle(ev("TICK", t(9, 31)))
    assert "R" in ctl.plan.assignment()


def test_cancellation_of_planned_request_forces_replan(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    without_b = [s for s in BASE if s[0] != "B"]
    res = ctl.handle(ev("QUEUE_UPDATED", t(9, 4), queue=mk_queue(net, without_b), trigger="cancellation"))
    assert res.forced and "B" not in ctl.plan.assignment()
    assert ctl.job_states["B"] == DispatchState.CANCELLED


def test_lock_release_requires_staff(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    rid = ctl.plan.routes["C1"][0]["request_id"]
    run_job(ctl, rid, "C1", t(9, 1))
    with pytest.raises(ValueError):
        ctl._set_state(rid, DispatchState.UNASSIGNED)
    res = ctl.handle(ev("STAFF_OVERRIDE", t(9, 2), request_id=rid, action="release_lock", approver="supervisor-7"))
    assert ctl.job_states[rid] in (DispatchState.PLANNED, DispatchState.UNASSIGNED)
    assert not ctl.crew_status["C1"].locked


def test_audit_log_written(ctl, net):
    ctl.handle(ev("QUEUE_UPDATED", t(9), queue=mk_queue(net, BASE), trigger="new_request"))
    lines = (ctl.run_dir / "events" / "events.jsonl").read_text().strip().splitlines()
    rec = json.loads(lines[-1])
    assert rec["event_type"] == "QUEUE_UPDATED" and rec["published"] and rec["simulated"] is True


def test_replay_simulation_end_to_end(net, router, cfg, catalog, tmp_path):
    crews = [Crew.from_record(crew_record("C1", "DOT", ["ROAD_REPAIR", "TRAFFIC_SIGNALS"], *node_latlon(net, 0, 0))),
             Crew.from_record(crew_record("C2", "DOT", ["ROAD_REPAIR", "TRAFFIC_SIGNALS"], *node_latlon(net, 7, 7)))]
    arrivals = []
    for i in range(14):
        code = ["RT001", "RT002", "RT005"][i % 3]
        la, lo = node_latlon(net, (i * 3) % 8, (i * 5) % 8)
        arrivals.append({"request_id": f"X{i:02d}", "agency": "DOT", "request_type_code": code,
                         "required_skill": catalog[code]["required_skill"], "est_service_minutes": catalog[code]["est_service_minutes"],
                         "latitude": la, "longitude": lo, "created_date": t(9) + (t(9, 6) - t(9)) * i})
    ctl = DispatchController(crews, router, cfg, catalog, tmp_path / "replay", run_id="replay", seed=2, maps=False)
    sim = ReplaySimulator(ctl, VendoredC11Provider(catalog), arrivals, cfg, seed=2,
                          scenario_events=[{"type": "CREW_STATUS", "time": t(10, 30), "crew_id": "C2", "available": False}])
    s = sim.run(t(9), t(19))
    assert s["locked_commitment_violations"] == 0
    assert s["completed_simulated"] >= 3
    assert all(a["simulated"] for a in ctl.audit if a["event_type"] in ("JOB_STATUS", "APPROVAL"))
    completed = [r for r, st in ctl.job_states.items() if st == DispatchState.COMPLETED]
    assert set(completed) == set(sim.completed)
