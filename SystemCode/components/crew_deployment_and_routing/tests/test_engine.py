import copy
import random

import numpy as np
import pytest

from crew_dispatch import engine as E
from crew_dispatch.engine import optimize_plan
from crew_dispatch.fixtures import LAT0, LON0, c11_record, crew_record, node_latlon, t
from crew_dispatch.schemas import Crew, CrewStatus, DispatchState, LockedJob, SchemaError, Snapshot


def queue(net, n=12, agency="DOT", skill="ROAD_REPAIR", seed=0, service=40, start_pos=1):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        la, lo = node_latlon(net, rng.randrange(8), rng.randrange(8))
        out.append(c11_record(f"{agency}{i}", agency, skill, la + 0.0002, lo, start_pos + i, service=service))
    return out


def plan(snap, router, cfg, catalog, **kw):
    return optimize_plan(snap, router, cfg, catalog, seed=kw.pop("seed", 7), **kw)


def test_input_records_preserved(net, router, cfg, catalog, crews):
    q = queue(net)
    original = copy.deepcopy(q)
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    assert q == original
    for rid, pr in p.requests.items():
        assert pr.record == next(r for r in original if r["request_id"] == rid)


def test_forbidden_priority_fields_do_not_change_plan(net, router, cfg, catalog, crews):
    q = queue(net, n=16)
    base = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    q2 = copy.deepcopy(q)
    for r in q2[::-1]:   # scramble every urgency-like field (reversed order) - must have no effect
        r["priority_band"] = random.choice(["ACT NOW", "MONITOR"])
        r["urgency_level"] = random.choice(["CRITICAL", "LOW"])
        r["priority_score"] = random.uniform(0, 100)
        r["queue_position"] = "C1"
        r["c11_urgency"] = "CRITICAL"
    other = plan(Snapshot(now=t(9), queue=q2, crews=crews, crew_status={}), router, cfg, catalog)
    assert base.sequences() == other.sequences()


def test_overall_queue_position_drives_selection(net, router, cfg, catalog):
    # one crew, capacity for ~2 jobs, 6 equidistant-ish requests: the top-ranked should be served
    crews = [Crew.from_record(crew_record("C1", "DOT", ["ROAD_REPAIR"], *node_latlon(net, 4, 4), budget=100))]
    q = queue(net, n=6, service=40)
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    served = set(p.assignment())
    assert "DOT0" in served and len(served) <= 2
    # reversing the ranks reverses who gets served
    q_rev = copy.deepcopy(q)
    for i, r in enumerate(q_rev):
        r["overall_queue_position"] = len(q_rev) - i
    p2 = plan(Snapshot(now=t(9), queue=q_rev, crews=crews, crew_status={}), router, cfg, catalog)
    assert "DOT5" in p2.assignment()


def test_held_and_review_excluded(net, router, cfg, catalog, crews):
    q = queue(net, n=4)
    q.append(c11_record("REV", "DOT", "ROAD_REPAIR", *node_latlon(net, 1, 1), 5, status="provisional_review"))
    q.append(c11_record("HLD", "DOT", "ROAD_REPAIR", *node_latlon(net, 1, 2), None, status="hold"))
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    assert "REV" not in p.assignment() and "HLD" not in p.assignment()
    assert p.pending_review == ["REV"] and p.held == ["HLD"]
    assert p.reasons["REV"] == [E.R_REVIEW] and p.reasons["HLD"] == [E.R_HELD]


def test_dispatch_approved_false_does_not_exclude(net, router, cfg, catalog, crews):
    q = queue(net, n=3)
    assert all(r["dispatch_approved"] is False for r in q)
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    assert len(p.assignment()) == 3


def test_agency_and_skill_compatibility(net, router, cfg, catalog, crews):
    q = queue(net, n=4) + queue(net, n=3, agency="DEP", skill="WATER_MAINS", seed=1, start_pos=5) \
        + [c11_record("SW", "DOT", "SIDEWALK_REPAIR", *node_latlon(net, 2, 2), 8)]
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    a = p.assignment()
    crew = {c.crew_id: c for c in crews}
    for rid, cid in a.items():
        pr = p.requests[rid]
        assert crew[cid].agency == pr.agency and set(pr.skills) <= crew[cid].skills
    assert a.get("SW") == "C2"     # only C2 has SIDEWALK_REPAIR


def test_multi_skill_requirement(net, router, cfg, catalog, crews):
    rec = c11_record("MS", "DOT", None, *node_latlon(net, 3, 3), 1, required_skills=["ROAD_REPAIR", "SIDEWALK_REPAIR"])
    rec.pop("required_skill")
    p = plan(Snapshot(now=t(9), queue=[rec], crews=crews, crew_status={}), router, cfg, catalog)
    assert p.assignment() == {"MS": "C2"}
    only_c1 = [c for c in crews if c.crew_id != "C2"]
    p2 = plan(Snapshot(now=t(9), queue=[rec], crews=only_c1, crew_status={}), router, cfg, catalog)
    assert p2.reasons["MS"] == [E.R_NO_CREW]


def test_unavailable_crew_reason(net, router, cfg, catalog, crews):
    rec = c11_record("SW", "DOT", "SIDEWALK_REPAIR", *node_latlon(net, 2, 2), 1)
    p = plan(Snapshot(now=t(9), queue=[rec], crews=crews, crew_status={"C2": CrewStatus("C2", available=False)}),
             router, cfg, catalog)
    assert p.reasons["SW"] == [E.R_NO_AVAIL]
    assert p.crews["C2"].reason == "crew_unavailable"


def test_insufficient_capacity_and_backlog(net, router, cfg, catalog):
    crews = [Crew.from_record(crew_record("C1", "DOT", ["ROAD_REPAIR"], *node_latlon(net, 0, 0), budget=130))]
    q = queue(net, n=10, service=40)
    q.append(c11_record("BIG", "DOT", "ROAD_REPAIR", *node_latlon(net, 5, 5), 11, service=500))
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    assert p.reasons["BIG"] == [E.R_TIME]
    assert 1 <= len(p.assignment()) <= 3
    unserved = [r for r in q if r["request_id"] not in p.assignment() and r["request_id"] != "BIG"]
    for r in unserved:
        assert p.reasons[r["request_id"]][0] in (E.R_CAPACITY, E.R_DEFERRED)
    assert p.metrics["backlog_weight"] > 0


def test_no_duplicates_and_work_time_accounting(net, router, cfg, catalog, crews):
    q = queue(net, n=20)
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog, keep_instances=True)
    ids = [s["request_id"] for st in p.routes.values() for s in st]
    assert len(ids) == len(set(ids))
    inst, sol = p.instances["DOT"], p.solutions["DOT"]
    for k, cid in enumerate(inst.crew_ids):
        travel, work, _, ok = inst.route_stats(k, sol.routes[k])
        assert ok and work <= p.crews[cid].cap_min + 1e-6
        stops = p.routes[cid]
        if stops:
            ret = inst.M[sol.routes[k][-1], inst.depot(k)]
            assert sum(s["travel_in_min"] for s in stops) + ret + sum(s["service_min"] for s in stops) == pytest.approx(work, abs=0.01)  # legs rounded to 1e-3 min
            # road-following legs reconstructed with A* match the matrix used by the optimiser
            for s in stops:
                assert s["leg_astar_min"] == pytest.approx(s["travel_in_min"], abs=1e-3)


def test_deterministic_with_seed(net, router, cfg, catalog, crews):
    q = queue(net, n=18)
    a = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog, seed=3)
    b = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog, seed=3)
    assert a.sequences() == b.sequences()


def test_unroutable_and_invalid(net, router, cfg, catalog, crews):
    q = [c11_record("FAR", "DOT", "ROAD_REPAIR", LAT0 - 0.02, LON0 - 0.02, 1),
         c11_record("NOXY", "DOT", "ROAD_REPAIR", None, None, 2),
         c11_record("BADRANK", "DOT", "ROAD_REPAIR", *node_latlon(net, 1, 1), 3)]
    q[2]["overall_queue_position"] = None
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)
    assert p.reasons["FAR"] == [E.R_UNROUTABLE] and p.reasons["NOXY"] == [E.R_UNROUTABLE]
    assert p.reasons["BADRANK"] == [E.R_INVALID]


def test_duplicate_request_ids_rejected(net, router, cfg, catalog, crews):
    q = queue(net, n=2)
    q[1]["request_id"] = q[0]["request_id"]
    with pytest.raises(SchemaError):
        plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog)


def test_locked_jobs_consume_capacity_and_are_not_replanned(net, router, cfg, catalog, crews):
    q = queue(net, n=8)
    la, lo = node_latlon(net, 6, 1)
    q.append(c11_record("LOCK", "DOT", "ROAD_REPAIR", la, lo, 1, service=60))
    st = CrewStatus("C1", lat=node_latlon(net, 0, 0)[0], lon=node_latlon(net, 0, 0)[1],
                    locked=[LockedJob("LOCK", "C1", DispatchState.IN_PROGRESS, la, lo, 60, est_travel_min=5, since=t(8, 50))])
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={"C1": st},
                      job_states={"LOCK": DispatchState.IN_PROGRESS}), router, cfg, catalog)
    assert "LOCK" not in p.assignment() and p.locked == ["LOCK"]
    cp = p.crews["C1"]
    assert cp.available_at == t(9, 50)                     # finishes at 08:50 + 60 min
    assert cp.reserved_locked_min == pytest.approx(65)
    assert (cp.origin_lat, cp.origin_lon) == (la, lo)       # plans from the job site


def test_overdue_in_progress_not_completed(net, router, cfg, catalog, crews):
    la, lo = node_latlon(net, 2, 2)
    q = [c11_record("LOCK", "DOT", "ROAD_REPAIR", la, lo, 1, service=30)]
    st = CrewStatus("C1", locked=[LockedJob("LOCK", "C1", DispatchState.IN_PROGRESS, la, lo, 30, since=t(7))])
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={"C1": st},
                      job_states={"LOCK": DispatchState.IN_PROGRESS}), router, cfg, catalog)
    assert p.crews["C1"].awaiting_update == ["LOCK"]
    assert p.crews["C1"].available_at == t(9, 15)            # now + overdue buffer, still locked


def test_locked_job_missing_from_queue_is_conflict(net, router, cfg, catalog, crews):
    la, lo = node_latlon(net, 2, 2)
    st = CrewStatus("C1", locked=[LockedJob("GONE", "C1", DispatchState.EN_ROUTE, la, lo, 30, est_travel_min=5, since=t(8, 55))])
    p = plan(Snapshot(now=t(9), queue=queue(net, n=2), crews=crews, crew_status={"C1": st}), router, cfg, catalog)
    assert any(c["request_id"] == "GONE" for c in p.locked_conflicts)


@pytest.mark.parametrize("method", ["rank_first_greedy", "travel_aware_greedy", "ga"])
def test_methods_feasible_and_comparable(net, router, cfg, catalog, crews, method):
    q = queue(net, n=25, seed=4)
    p = plan(Snapshot(now=t(9), queue=q, crews=crews, crew_status={}), router, cfg, catalog, method=method, keep_instances=True)
    assert all(s.feasible() for s in p.solutions.values())


def test_ga_not_worse_than_baselines_on_objective(net, router, cfg, catalog, crews):
    q = queue(net, n=30, seed=5)
    snap = Snapshot(now=t(9), queue=q, crews=crews, crew_status={})
    f = {m: sum(s.fitness() for s in plan(snap, router, cfg, catalog, method=m, keep_instances=True).solutions.values())
         for m in ("rank_first_greedy", "travel_aware_greedy", "ga")}
    assert f["ga"] <= min(f["rank_first_greedy"], f["travel_aware_greedy"]) + 1e-6
