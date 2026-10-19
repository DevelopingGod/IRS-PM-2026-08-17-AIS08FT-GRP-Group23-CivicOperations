"""Versioned, machine-readable plan documents (JSON) and route geometry (GeoJSON).

Each accepted planning event produces ``plan_v####.json`` (+ ``routes_v####.geojson``). Files are
never overwritten. Upstream request records are reproduced verbatim under ``request``; everything
Dispatch generates lives under ``dispatch``.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import numpy as np

from .schemas import SCHEMA_VERSION, DispatchState


def _jsonable(o):
    if isinstance(o, datetime):
        return o.isoformat()
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return None if not np.isfinite(o) else float(o)
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, (set, frozenset, tuple)):
        return sorted(o) if isinstance(o, (set, frozenset)) else list(o)
    if isinstance(o, DispatchState):
        return o.value
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


def dumps(doc) -> str:
    return json.dumps(doc, indent=2, default=_jsonable, ensure_ascii=False)


def plan_changes(prev_assign: dict, prev_seq: dict, new_assign: dict, new_seq: dict, executed=()) -> dict:
    """Diff of future (unlocked) work. Requests that left the plan because they were approved,
    started or completed (``executed``) are execution progress, not plan churn."""
    executed = set(executed)
    prev_seq = {c: [r for r in seq if r not in executed] for c, seq in prev_seq.items()}
    newly = sorted(r for r in new_assign if r not in prev_assign)
    into_execution = sorted(r for r in prev_assign if r not in new_assign and r in executed)
    dropped = sorted(r for r in prev_assign if r not in new_assign and r not in executed)
    moved = sorted(r for r in new_assign if r in prev_assign and prev_assign[r] != new_assign[r])
    reseq = sorted(c for c in new_seq if new_seq.get(c) and prev_seq.get(c)
                   and set(new_seq[c]) == set(prev_seq[c]) and new_seq[c] != prev_seq[c])
    kept = sum(1 for r in new_assign if prev_assign.get(r) == new_assign[r])
    return {"newly_assigned": newly, "no_longer_assigned": dropped, "moved_into_execution": into_execution,
            "moved_to_other_crew": moved,
            "resequenced_crews": reseq, "unchanged_assignments": kept,
            "changed_assignment_count": len(dropped) + len(moved),
            "material_change": bool(newly or dropped or moved or reseq)}


def build_plan_document(plan, snapshot, *, version: int, run_id: str, event: dict, cfg: dict, data_versions: dict,
                        c11_info: dict, prev_doc: dict | None, job_states: dict, crews: dict,
                        geometry_file: str | None, extra_diagnostics: dict | None = None) -> dict:
    assign = plan.assignment()
    seqs = plan.sequences()
    prev_assign = {a["request_id"]: a["crew_id"] for a in (prev_doc or {}).get("assignments", [])}
    prev_seq = {c["crew_id"]: [s["request_id"] for s in c["planned_stops"]] for c in (prev_doc or {}).get("crews", [])}
    from .schemas import LOCKED_STATES, DispatchState
    executed = {r for r, st in job_states.items() if st in LOCKED_STATES or st == DispatchState.COMPLETED}
    changes = plan_changes(prev_assign, prev_seq, assign, seqs, executed)

    crew_docs, assignments = [], []
    for cid, cp in plan.crews.items():
        stops = plan.routes.get(cid, [])
        clean = []
        for seq_no, s in enumerate(stops, start=1):
            d = {k: v for k, v in s.items() if not k.startswith("_")}
            d["sequence"] = seq_no
            clean.append(d)
            assignments.append({"request_id": s["request_id"], "crew_id": cid, "sequence": seq_no,
                                "est_start": s["est_start"], "est_finish": s["est_finish"],
                                "travel_in_min": s["travel_in_min"], "service_min": s["service_min"]})
        travel = sum(s["travel_in_min"] for s in stops) + (stops[-1].get("return_travel_min", 0.0) if stops else 0.0)
        work = travel + sum(s["service_min"] for s in stops)
        crew_docs.append({
            "crew_id": cid, "agency": cp.agency, "skills": sorted(crews[cid].skills), "usable": cp.usable,
            "unusable_reason": cp.reason, "current_position": {"lat": cp.current_lat, "lon": cp.current_lon},
            "depot": {"lat": crews[cid].depot_lat, "lon": crews[cid].depot_lon},
            "planning_origin": {"lat": cp.origin_lat, "lon": cp.origin_lon},
            "available_at": cp.available_at, "duty_end": cp.duty_end,
            "budget_min": cp.budget_min, "worked_min": round(cp.worked_min, 3), "reserved_by_locked_min": round(cp.reserved_locked_min, 3),
            "capacity_for_new_work_min": round(cp.cap_min, 3), "planned_work_min": round(work, 3),
            "remaining_after_plan_min": round(cp.cap_min - work, 3), "planned_travel_min": round(travel, 3),
            "locked_commitments": [{"request_id": j.request_id, "state": j.state.value, "since": j.since,
                                    "lat": j.lat, "lon": j.lon, "approval_ref": j.approval_ref} for j in cp.locked],
            "awaiting_status_update": cp.awaiting_update,
            "planned_stops": clean})

    req_docs = []
    for rid, pr in plan.requests.items():
        state = job_states.get(rid)
        req_docs.append({
            "request": pr.record,
            "dispatch": {"execution_state": state.value if state else None, "c11_queue_status": pr.queue_status,
                         "overall_queue_position_used": pr.rank if pr.queue_status == "provisional" else None,
                         "rank_weight": pr.weight, "crew_id": assign.get(rid),
                         "sequence": (seqs[assign[rid]].index(rid) + 1) if rid in assign else None,
                         "reasons": plan.reasons.get(rid, []), "notes": pr.notes, "snap_distance_m": pr.snap_m,
                         "derived_inputs": {"required_skills": list(pr.skills), "service_min": pr.service_min}}})

    unassigned = [{"request_id": rid, "overall_queue_position": plan.requests[rid].rank, "reasons": rs,
                   "notes": plan.requests[rid].notes}
                  for rid, rs in plan.reasons.items() if rid in plan.requests and rid not in plan.held and rid not in plan.pending_review]
    return {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "plan_version": version,
        "plan_id": f"{run_id}-v{version:04d}",
        "authorization": {"mode": cfg.get("mode", "proposal_only"), "dispatch_authorized": False,
                          "statement": "Proposed plan only. Neither C11 nor this optimiser authorises dispatch; "
                                       "execution requires a verified staff-portal approval per job."},
        "event": event, "planning_time": plan.now,
        "data_versions": data_versions, "c11_source": c11_info,
        "algorithm": {"method": plan.method, "seed": plan.seed, "objective": cfg["objective"],
                      "ga": cfg["ga"] if plan.method == "ga" else None, "routing": cfg["routing"],
                      "capacity": cfg["capacity"], "operating_hours": cfg["operating_hours"]},
        "metrics": plan.metrics, "metrics_by_agency": plan.per_agency,
        "crews": crew_docs, "assignments": assignments,
        "unassigned": unassigned, "pending_review": plan.pending_review, "held": plan.held,
        "locked_commitments": plan.locked, "locked_conflicts": plan.locked_conflicts,
        "changes_from_previous": changes,
        "route_geometry_file": geometry_file,
        "diagnostics": {**plan.diagnostics, **(extra_diagnostics or {})},
        "requests": req_docs,
    }


def route_geojson(plan, router, version: int) -> dict:
    feats = []
    for cid, cp in plan.crews.items():
        if cp.locked_edges:
            xy = router.path_geometry(cp.locked_edges)
            if len(xy) >= 2:
                feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": np.round(xy, 6).tolist()},
                              "properties": {"crew_id": cid, "kind": "locked_leg",
                                             "to_request": [j.request_id for j in cp.locked]}})
    for cid, stops in plan.routes.items():
        for seq_no, s in enumerate(stops, start=1):
            if s.get("_path_edges") is not None:
                xy = router.path_geometry(s["_path_edges"])
                if len(xy) >= 2:
                    feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": np.round(xy, 6).tolist()},
                                  "properties": {"crew_id": cid, "kind": "leg", "sequence": seq_no, "to_request": s["request_id"],
                                                 "travel_min": s["travel_in_min"], "distance_m": s.get("leg_distance_m")}})
            if s.get("_return_edges") is not None:
                xy = router.path_geometry(s["_return_edges"])
                if len(xy) >= 2:
                    feats.append({"type": "Feature", "geometry": {"type": "LineString", "coordinates": np.round(xy, 6).tolist()},
                                  "properties": {"crew_id": cid, "kind": "return_to_depot", "travel_min": s.get("return_travel_min")}})
    return {"type": "FeatureCollection", "properties": {"plan_version": version,
            "attribution": "Road geometry (c) OpenStreetMap contributors, ODbL 1.0"}, "features": feats}


def write_new(path: Path, text: str) -> None:
    """Write a file that must not already exist (plan versions are immutable)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "x", encoding="utf-8") as f:
        f.write(text)
