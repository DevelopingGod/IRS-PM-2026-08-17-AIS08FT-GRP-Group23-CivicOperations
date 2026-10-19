"""Optimisation engine: Snapshot + config + seed -> reproducible proposed plan.

The engine is stateless. It (1) separates the C11 queue into eligible / pending-review / held /
locked requests, (2) derives each crew's planning origin, availability and capacity from its locked
commitments, (3) builds one optimisation instance per agency (agency compatibility is a hard
constraint, so this decomposition is exact), (4) solves it with the requested method and (5) explains
every unserved request. Priority input: ``overall_queue_position`` only.
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

from . import timepolicy
from .optimize.exact import solve_exact
from .optimize.ga import GAConfig, solve_ga
from .optimize.greedy import rank_first_greedy, travel_aware_greedy
from .optimize.instance import Instance, Solution, best_insertions, fill_greedy
from .optimize.metrics import solution_metrics
from .optimize.weights import rank_weights
from .schemas import (LOCKED_STATES, TERMINAL_STATES, CrewStatus, DispatchState, SchemaError, Snapshot,
                      add_minutes, minutes_between)

METHODS = ("ga", "rank_first_greedy", "travel_aware_greedy", "exact", "warm_insertion")

# Unserved / excluded reason categories (a request may carry several).
R_HELD = "HELD_BY_C11"
R_REVIEW = "PENDING_HUMAN_REVIEW"
R_UNROUTABLE = "UNROUTABLE_LOCATION"
R_NO_CREW = "NO_COMPATIBLE_CREW"
R_NO_AVAIL = "NO_AVAILABLE_COMPATIBLE_CREW"
R_TIME = "INSUFFICIENT_TIME_CAPACITY"          # no usable compatible crew could serve it even as its only job
R_CAPACITY = "CAPACITY_USED_BY_OTHER_REQUESTS"  # feasible alone, but no room left without displacing planned work
R_DEFERRED = "DEFERRED_BY_OPTIMIZER"            # room exists, optimiser judged it not worth its cost now
R_INVALID = "INVALID_INPUT"
R_TERMINAL = "ALREADY_COMPLETED_OR_CANCELLED"


def stable_seed(*parts) -> int:
    return int(hashlib.sha256("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)


@dataclass
class PlanningRequest:
    request_id: str
    agency: str
    skills: tuple
    lat: float
    lon: float
    service_min: float
    rank: int | None
    queue_status: str
    record: dict
    notes: list = field(default_factory=list)
    snap_m: float | None = None
    weight: float | None = None


@dataclass
class CrewPlan:
    crew_id: str
    agency: str
    usable: bool
    reason: str | None
    origin_lat: float
    origin_lon: float
    available_at: datetime
    duty_end: datetime | None
    budget_min: float
    worked_min: float
    reserved_locked_min: float
    cap_min: float
    locked: list
    awaiting_update: list
    current_lat: float | None = None
    current_lon: float | None = None
    locked_edges: list = field(default_factory=list)   # road path current position -> locked job(s)


@dataclass
class PlanResult:
    now: datetime
    method: str
    seed: int
    requests: dict                 # request_id -> PlanningRequest
    crews: dict                    # crew_id -> CrewPlan
    routes: dict                   # crew_id -> list of stop dicts (future, unlocked)
    reasons: dict                  # request_id -> list of reasons (unserved / excluded)
    pending_review: list
    held: list
    locked: list
    locked_conflicts: list
    metrics: dict
    per_agency: dict
    diagnostics: dict
    instances: dict = field(default_factory=dict)
    solutions: dict = field(default_factory=dict)

    def assignment(self) -> dict:
        return {s["request_id"]: cid for cid, stops in self.routes.items() for s in stops}

    def sequences(self) -> dict:
        return {cid: [s["request_id"] for s in stops] for cid, stops in self.routes.items()}


# ---------------------------------------------------------------------- input normalisation
def _float(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def normalise_request(rec: dict, catalog: dict, cfg: dict) -> PlanningRequest:
    rid = str(rec.get("request_id") or "").strip()
    if not rid:
        raise SchemaError("C11 record without request_id")
    cat = catalog.get(rec.get("request_type_code"), {})
    notes = []
    skills = rec.get("required_skills")
    if isinstance(skills, str):
        skills = [s.strip() for s in skills.replace("|", ",").split(",") if s.strip()]
    if not skills:
        skills = [rec["required_skill"]] if rec.get("required_skill") else ([cat["required_skill"]] if cat else [])
        if not rec.get("required_skill") and cat:
            notes.append("required_skill from catalog")
    service = _float(rec.get("est_service_minutes"))
    if service is None or service <= 0:
        service = cat.get("est_service_minutes") if cat else None
        notes.append("est_service_minutes from catalog" if service else "est_service_minutes default")
        service = float(service or cfg["capacity"]["default_service_min"])
    rank = rec.get("overall_queue_position")
    status = rec.get("c11_queue_status")
    return PlanningRequest(request_id=rid, agency=str(rec.get("agency") or "").strip(), skills=tuple(sorted(set(skills))),
                           lat=_float(rec.get("latitude", rec.get("lat"))), lon=_float(rec.get("longitude", rec.get("lon"))),
                           service_min=float(service), rank=rank, queue_status=status, record=rec, notes=notes)


# ---------------------------------------------------------------------- crew availability
def crew_planning_state(crew, status: CrewStatus | None, now: datetime, cfg: dict, router) -> CrewPlan:
    status = status or CrewStatus(crew_id=crew.crew_id)
    if status.duty_start is None or status.duty_end is None:
        ds, de = timepolicy.service_period(cfg, crew.agency, now)
    else:
        ds, de = status.duty_start, status.duty_end
    lat = status.lat if status.lat is not None else crew.depot_lat
    lon = status.lon if status.lon is not None else crew.depot_lon
    cur_lat, cur_lon = lat, lon
    t_free = max(now, ds)
    reserved = 0.0
    awaiting = []
    locked_out = []
    buffer = float(cfg["dynamic"]["overdue_buffer_min"])
    for job in status.locked:
        if job.state == DispatchState.IN_PROGRESS:
            finish = add_minutes(job.since or now, job.service_min)
            if finish <= now:
                awaiting.append(job.request_id)       # estimate passed: NOT completed until confirmed
                finish = add_minutes(now, buffer)
            t_free = max(t_free, finish)
        elif job.state == DispatchState.EN_ROUTE:
            arrive = add_minutes(job.since or now, job.est_travel_min)
            if arrive <= now:
                awaiting.append(job.request_id)
                arrive = now
            t_free = add_minutes(max(t_free, arrive), job.service_min)
        else:  # APPROVED_NOT_STARTED: crew goes there next, after earlier locked work
            sn = router.snap([lat, job.lat], [lon, job.lon])
            leg = job.est_travel_min
            if sn.ok.all():
                leg = float(router.matrix_minutes([sn.node[0]], [sn.node[1]])[0, 0])
            job.est_travel_min = leg
            t_free = add_minutes(t_free, leg + job.service_min)
        reserved += job.est_travel_min + job.service_min
        lat, lon = job.lat, job.lon
        locked_out.append(job)
    budget_left = crew.work_time_budget_min - status.worked_min - reserved
    cap = min(budget_left, minutes_between(t_free, de)) if de else budget_left
    usable, reason = True, None
    if not status.available:
        usable, reason = False, "crew_unavailable"
    elif now >= de:
        usable, reason = False, "off_duty"
    elif cap <= 0:
        usable, reason = False, "no_remaining_capacity"
    return CrewPlan(crew_id=crew.crew_id, agency=crew.agency, usable=usable, reason=reason, origin_lat=lat, origin_lon=lon,
                    available_at=t_free, duty_end=de, budget_min=crew.work_time_budget_min, worked_min=status.worked_min,
                    reserved_locked_min=reserved, cap_min=max(0.0, cap), locked=locked_out, awaiting_update=awaiting,
                    current_lat=cur_lat, current_lon=cur_lon)


# ---------------------------------------------------------------------- main entry point
def optimize_plan(snapshot: Snapshot, router, cfg: dict, catalog: dict, seed: int = 0, method: str = "ga",
                  ga_overrides: dict | None = None, keep_instances: bool = False, reconstruct_paths: bool = True) -> PlanResult:
    if method not in METHODS:
        raise ValueError(f"method must be one of {METHODS}")
    if snapshot.now.tzinfo is None:
        raise SchemaError("snapshot.now must be timezone-aware")
    t0 = time.perf_counter()
    now = snapshot.now
    reasons: dict = {}
    requests: dict = {}
    pending, held, locked_ids, conflicts = [], [], [], []

    # 1. queue partition -------------------------------------------------------------------
    locked_by_request = {j.request_id: (cid, j) for cid, st in snapshot.crew_status.items() for j in st.locked}
    seen = set()
    candidates = []
    for rec in snapshot.queue:
        pr = normalise_request(rec, catalog, cfg)
        if pr.request_id in seen:
            raise SchemaError(f"duplicate request_id {pr.request_id} in C11 queue")
        seen.add(pr.request_id)
        requests[pr.request_id] = pr
        state = snapshot.job_states.get(pr.request_id)
        if pr.request_id in locked_by_request or state in LOCKED_STATES:
            locked_ids.append(pr.request_id)
            if pr.queue_status == "hold":
                conflicts.append({"request_id": pr.request_id, "issue": "locked job is now HOLD in C11; staff decision required"})
            continue
        if state in TERMINAL_STATES:
            reasons[pr.request_id] = [R_TERMINAL]
            continue
        if pr.queue_status == "hold":
            held.append(pr.request_id)
            reasons[pr.request_id] = [R_HELD]
        elif pr.queue_status == "provisional_review":
            pending.append(pr.request_id)
            reasons[pr.request_id] = [R_REVIEW]
        elif pr.queue_status == "provisional":
            if not isinstance(pr.rank, (int, np.integer)) or isinstance(pr.rank, bool) or pr.rank < 1:
                reasons[pr.request_id] = [R_INVALID]
                pr.notes.append("provisional request without a valid overall_queue_position")
            elif not pr.agency or not pr.skills:
                reasons[pr.request_id] = [R_INVALID]
                pr.notes.append("agency or required skill missing")
            else:
                candidates.append(pr)
        else:
            reasons[pr.request_id] = [R_INVALID]
            pr.notes.append(f"unknown c11_queue_status {pr.queue_status!r}")
    for rid, (cid, job) in locked_by_request.items():
        if rid not in seen:
            conflicts.append({"request_id": rid, "crew_id": cid,
                              "issue": "locked job no longer in C11 queue (cancelled/closed upstream?); crew NOT redirected automatically"})

    # 2. crews -------------------------------------------------------------------------------
    crew_plans = {c.crew_id: crew_planning_state(c, snapshot.crew_status.get(c.crew_id), now, cfg, router) for c in snapshot.crews}
    crews_by_id = {c.crew_id: c for c in snapshot.crews}
    usable = [c for c in snapshot.crews if crew_plans[c.crew_id].usable]
    if usable:
        sn_o = router.snap([crew_plans[c.crew_id].origin_lat for c in usable], [crew_plans[c.crew_id].origin_lon for c in usable])
        sn_d = router.snap([c.depot_lat for c in usable], [c.depot_lon for c in usable])
    crew_nodes = {}
    for idx, c in enumerate(usable):
        if not (sn_o.ok[idx] and sn_d.ok[idx]):
            crew_plans[c.crew_id].usable = False
            crew_plans[c.crew_id].reason = "unroutable_crew_location"
        else:
            crew_nodes[c.crew_id] = (int(sn_o.node[idx]), int(sn_d.node[idx]))
    usable = [c for c in usable if crew_plans[c.crew_id].usable]

    # 3. requests: routability, compatibility ------------------------------------------------
    sn_r = router.snap([p.lat if p.lat is not None else np.nan for p in candidates],
                       [p.lon if p.lon is not None else np.nan for p in candidates]) if candidates else None
    req_node = {}
    routable = []
    for idx, p in enumerate(candidates):
        if sn_r.ok[idx]:
            req_node[p.request_id] = int(sn_r.node[idx])
            p.snap_m = float(sn_r.distance_m[idx])
            routable.append(p)
        else:
            reasons[p.request_id] = [R_UNROUTABLE]
            p.notes.append(sn_r.reason[idx])

    n_ranked = sum(1 for r in snapshot.queue if r.get("c11_queue_status") != "hold")
    wcfg = cfg["objective"]["rank_weight"]
    if candidates:
        ws = rank_weights([int(p.rank) for p in candidates], wcfg["transform"], float(wcfg.get("param", 25)), n_ranked)
        for p, w in zip(candidates, ws):
            p.weight = float(w)
    lam_b = 1e6 if cfg["objective"].get("mode") == "lexicographic" else float(cfg["objective"]["lambda_backlog"])
    ga_cfg = GAConfig.from_dict({**cfg["ga"], **(ga_overrides or {})})
    routes, per_agency, instances, solutions = {}, {}, {}, {}
    agencies = sorted({p.agency for p in routable})
    for agency in agencies:
        reqs = [p for p in routable if p.agency == agency]
        roster = [c for c in snapshot.crews if c.agency == agency]
        crews = [c for c in usable if c.agency == agency]
        keep = []
        for p in reqs:
            need = set(p.skills)
            if not any(need <= c.skills for c in roster):
                reasons[p.request_id] = [R_NO_CREW]
            elif not any(need <= c.skills for c in crews):
                reasons[p.request_id] = [R_NO_AVAIL]
            else:
                keep.append(p)
        if not keep:
            continue
        n, K = len(keep), len(crews)
        nodes = [req_node[p.request_id] for p in keep] + [crew_nodes[c.crew_id][0] for c in crews] + [crew_nodes[c.crew_id][1] for c in crews]
        M = router.matrix_minutes(nodes, nodes)
        if not cfg["capacity"].get("return_to_depot", True):
            M[:, n + K:] = 0.0
        compat = np.array([[set(p.skills) <= c.skills for p in keep] for c in crews], bool)
        cp = [crew_plans[c.crew_id] for c in crews]
        avail = np.array([minutes_between(now, x.available_at) for x in cp])
        cap = np.array([x.cap_min for x in cp])
        service = np.array([p.service_min for p in keep])
        # alone-feasibility: origin -> i -> depot within capacity
        alone = np.array([[M[n + k, i] + service[i] + M[i, n + K + k] <= cap[k] + 1e-9 for i in range(n)] for k in range(K)], bool)
        compat_f = compat & alone
        for i, p in enumerate(keep):
            if not compat_f[:, i].any():
                reasons[p.request_id] = [R_TIME]
        ranks = np.array([int(p.rank) for p in keep], float)
        prev = np.array([next((k for k, c in enumerate(crews) if c.crew_id == snapshot.previous_assignment.get(p.request_id)), -1)
                         for p in keep], np.int64)
        inst = Instance(agency=agency, req_ids=[p.request_id for p in keep], crew_ids=[c.crew_id for c in crews],
                        weight=rank_weights(ranks, wcfg["transform"], float(wcfg.get("param", 25)), n_ranked),
                        rank=ranks, service=service, compat=compat_f, M=M, avail=avail, cap=cap, prev_crew=prev,
                        lambda_backlog=lam_b, lambda_delay=float(cfg["objective"]["lambda_delay"]),
                        lambda_change=float(cfg["objective"]["lambda_change"]))
        t1 = time.perf_counter()
        info = {}
        if method == "ga":
            warm = []
            if cfg["dynamic"].get("warm_start", True) and snapshot.previous_routes:
                warm = [_warm_start(inst, snapshot.previous_routes)]
            res = solve_ga(inst, ga_cfg, stable_seed(seed, agency, now.isoformat()), warm)
            sol = res.best
            info = {"generations": res.generations, "evaluations": res.evaluations, "terminated_by": res.terminated_by,
                    "initial_population_sources": res.seeded_from, "fitness_first": res.history[0], "fitness_best": res.history[-1]}
        elif method == "rank_first_greedy":
            sol = rank_first_greedy(inst)
        elif method == "travel_aware_greedy":
            sol = travel_aware_greedy(inst)
        elif method == "warm_insertion":   # insertion-only policy: keep previous routes, insert new work
            sol = fill_greedy(_warm_start(inst, snapshot.previous_routes), np.argsort(inst.rank, kind="stable"))
        else:
            sol = solve_exact(inst)
        assert sol.feasible()
        m = solution_metrics(sol)
        m["runtime_s"] = time.perf_counter() - t1
        m.update(info)
        per_agency[agency] = m
        a = sol.assigned()
        for i in np.nonzero(a < 0)[0]:
            rid = inst.req_ids[int(i)]
            if rid not in reasons:
                room = any(best_insertions(inst, sol, int(k), np.asarray([int(i)]))[3][0] for k in inst.compatible_crews(int(i)))
                reasons[rid] = [R_DEFERRED if room else R_CAPACITY]
        for k, cid in enumerate(inst.crew_ids):
            routes[cid] = [_stop(inst, k, stop, now, requests) for stop in inst.schedule(k, sol.routes[k])]
        if keep_instances:
            instances[agency], solutions[agency] = inst, sol

    if reconstruct_paths:
        _attach_paths(routes, crew_plans, crew_nodes, req_node, router, crews_by_id)

    metrics = aggregate_metrics(per_agency, reasons, candidates, {s["request_id"] for st in routes.values() for s in st})
    diagnostics = {"runtime_s": time.perf_counter() - t0, "method": method, "seed": seed,
                   "ga_config": ga_cfg.__dict__ if method == "ga" else None,
                   "lambda_backlog_effective": lam_b, "n_queue": len(snapshot.queue), "n_ranked_active": n_ranked}
    return PlanResult(now=now, method=method, seed=seed, requests=requests, crews=crew_plans, routes=routes, reasons=reasons,
                      pending_review=pending, held=held, locked=locked_ids, locked_conflicts=conflicts, metrics=metrics,
                      per_agency=per_agency, diagnostics=diagnostics, instances=instances, solutions=solutions)


def _warm_start(inst: Instance, previous_routes: dict) -> Solution:
    idx = {rid: i for i, rid in enumerate(inst.req_ids)}
    routes = []
    for k, cid in enumerate(inst.crew_ids):
        seq = [idx[r] for r in previous_routes.get(cid, []) if r in idx and inst.compat[k, idx[r]]]
        while seq and not inst.route_stats(k, seq)[3]:
            seq = seq[:-1]
        routes.append(seq)
    used = set()
    for k in range(len(routes)):
        routes[k] = [i for i in routes[k] if not (i in used or used.add(i))]
    return Solution(inst, routes)


def _stop(inst, k, stop, now, requests) -> dict:
    i, leg, st, fin = stop
    rid = inst.req_ids[i]
    return {"request_id": rid, "travel_in_min": round(leg, 3), "est_start": add_minutes(now, st).isoformat(),
            "est_finish": add_minutes(now, fin).isoformat(), "service_min": float(inst.service[i]),
            "overall_queue_position": int(inst.rank[i]), "rank_weight": round(float(inst.weight[i]), 6),
            "_start_min": st, "_finish_min": fin}


def _attach_paths(routes, crew_plans, crew_nodes, req_node, router, crews_by_id):
    """Reconstruct road-following legs with A* for the final plan only (not inside the GA)."""
    heuristic = "alt" if getattr(router, "landmarks", None) else "haversine"
    for cid, cp in crew_plans.items():      # current position -> locked jobs (display only)
        travelling = [j for j in cp.locked if j.state != DispatchState.IN_PROGRESS]
        if cp.locked and travelling and cp.current_lat is not None:
            pts = [(cp.current_lat, cp.current_lon)] + [(j.lat, j.lon) for j in cp.locked]
            sn = router.snap([p[0] for p in pts], [p[1] for p in pts])
            edges = []
            for a, b in zip(sn.node[:-1], sn.node[1:]):
                if a >= 0 and b >= 0:
                    edges += router.astar(int(a), int(b), heuristic=heuristic).edges
            cp.locked_edges = edges
    for cid, stops in routes.items():
        if not stops:
            continue
        o, d = crew_nodes[cid]
        prev = o
        for s in stops:
            node = req_node[s["request_id"]]
            p = router.astar(prev, node, heuristic=heuristic)
            s["_path_edges"] = p.edges
            s["leg_distance_m"] = round(router.path_length_m(p.edges), 1)
            s["leg_astar_min"] = round(p.cost_s / 60.0, 3)
            prev = node
        p = router.astar(prev, d, heuristic=heuristic)
        stops[-1]["_return_edges"] = p.edges
        stops[-1]["return_distance_m"] = round(router.path_length_m(p.edges), 1)
        stops[-1]["return_travel_min"] = round(p.cost_s / 60.0, 3)


def aggregate_metrics(per_agency: dict, reasons: dict, candidates: list, served_ids: set) -> dict:
    """Plan-level metrics. 'served' = planned within the current horizon (not started/completed)."""
    keys_sum = ("served", "travel_min", "service_min", "work_min", "weighted_start_h", "changes",
                "avoidable_inversion_pairs", "bypassed_requests", "crews_used")
    agg = {k: float(sum(m.get(k, 0) or 0 for m in per_agency.values())) for k in keys_sum}
    total_w = sum(p.weight for p in candidates)
    served_w = sum(p.weight for p in candidates if p.request_id in served_ids)
    ranks_served = [p.rank for p in candidates if p.request_id in served_ids]
    ranks_unserved = [p.rank for p in candidates if p.request_id not in served_ids]
    reason_counts: dict = {}
    for rs in reasons.values():
        for r in rs:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    agg.update({
        "provisional_candidates": len(candidates),
        "served_pct_of_candidates": 100.0 * agg["served"] / len(candidates) if candidates else 100.0,
        "candidate_weight": total_w, "served_weight": served_w, "backlog_weight": total_w - served_w,
        "weighted_served_share": served_w / total_w if total_w else 1.0,
        "median_rank_served": float(np.median(ranks_served)) if ranks_served else None,
        "median_rank_unserved": float(np.median(ranks_unserved)) if ranks_unserved else None,
        "travel_per_served_min": agg["travel_min"] / agg["served"] if agg["served"] else 0.0,
        "unserved_reason_counts": reason_counts,
        "runtime_s_optimiser": float(sum(m.get("runtime_s", 0) for m in per_agency.values())),
    })
    return agg
