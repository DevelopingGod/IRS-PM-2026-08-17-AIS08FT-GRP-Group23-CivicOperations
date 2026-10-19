"""Dispatch controller: event processing, locked commitments, replanning policy, plan versioning, audit.

Events are plain dicts: {"type", "time" (tz-aware datetime), "source", "simulated", ...}.

QUEUE_UPDATED   {"queue": <C11 output>, "trigger": "new_request"|"periodic"|"review_completed"|...}
TICK            periodic planning check
JOB_STATUS      {"request_id", "crew_id", "state": EN_ROUTE|IN_PROGRESS|COMPLETED|CANCELLED,
                 "actual_travel_min"?, "actual_service_min"?}   (Crew Portal or simulated Crew Portal)
APPROVAL        {"request_id", "crew_id", "approver", "verified": bool, "approval_ref"}  (staff portal)
CREW_STATUS     {"crew_id", "available": bool, "lat"?, "lon"?}
STAFF_OVERRIDE  {"request_id", "action": "release_lock", "approver"}  (explicit staff intervention)

Rules enforced here (tested in tests/test_dynamic.py):
* the optimiser never touches APPROVED_NOT_STARTED / EN_ROUTE / IN_PROGRESS / COMPLETED jobs;
* a job is COMPLETED only through a JOB_STATUS event - an elapsed estimate only flags it as awaiting update;
* approvals must be verified by an authoritative source; C11's dispatch_approved=False is ignored (never
  used to exclude work from proposals, never treated as approval);
* non-forced replans are published only when the objective improves by the configured threshold.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .. import timepolicy
from ..engine import optimize_plan
from ..outputs import build_plan_document, dumps, route_geojson, write_new
from ..schemas import (ALLOWED_TRANSITIONS, LOCKED_STATES, CrewStatus, DispatchState, LockedJob, Snapshot,
                       minutes_between)
from ..upstream.c11_provider import validate_c11_output


@dataclass
class ControllerResult:
    event_type: str
    replanned: bool = False
    published: bool = False
    plan_version: int | None = None
    reason: str = ""
    forced: bool = False
    changes: dict | None = None
    runtime_s: float = 0.0
    map_paths: list = field(default_factory=list)


class DispatchController:
    def __init__(self, crews, router, cfg, catalog, run_dir, *, run_id: str, seed: int = 0, method: str = "ga",
                 c11_info: dict | None = None, data_versions: dict | None = None, maps: bool = True, base_map=None,
                 replan_method: str | None = None, ga_replan_overrides: dict | None = None, write_outputs: bool = True):
        self.crews = {c.crew_id: c for c in crews}
        self.router = router
        self.cfg = cfg
        self.catalog = catalog
        self.run_dir = Path(run_dir)
        self.run_id = run_id
        self.seed = seed
        self.method = method
        self.replan_method = replan_method or method
        self.ga_replan_overrides = ga_replan_overrides or {"generations": cfg["dynamic"].get("ga_generations_replan", 60)}
        self.c11_info = c11_info or {}
        self.data_versions = data_versions or {}
        self.maps = maps and cfg["maps"].get("enabled", True)
        self.base_map = base_map
        self.write_outputs = write_outputs
        self.crew_status: dict = {cid: CrewStatus(crew_id=cid) for cid in self.crews}
        self.job_states: dict = {}
        self.job_crew: dict = {}
        self.queue: list = []
        self.queue_index: dict = {}
        self.plan = None
        self.doc = None
        self.geo = None
        self.prev_doc = None
        self.prev_geo = None
        self.version = 0
        self.dirty: set = set()
        self.forced_reasons: set = set()
        self.last_replan: datetime | None = None
        self.maps_written = 0
        self.locked_violations = 0
        self.audit: list = []
        self.replan_log: list = []
        if write_outputs:
            (self.run_dir / "plans").mkdir(parents=True, exist_ok=True)
            (self.run_dir / "events").mkdir(parents=True, exist_ok=True)
        self._events_path = self.run_dir / "events" / "events.jsonl"

    # ------------------------------------------------------------------ duty windows
    def start_duty(self, now: datetime) -> None:
        """Assign each crew its duty period (agency operating hours, option C)."""
        for cid, crew in self.crews.items():
            st = self.crew_status[cid]
            st.duty_start, st.duty_end = timepolicy.service_period(self.cfg, crew.agency, now)

    # ------------------------------------------------------------------ audit
    def _log(self, event: dict, result: ControllerResult, extra: dict | None = None) -> None:
        rec = {"event_type": event["type"], "time": event["time"], "source": event.get("source"),
               "simulated": bool(event.get("simulated", False)),
               "detail": {k: v for k, v in event.items() if k not in ("type", "time", "source", "simulated", "queue")},
               "replanned": result.replanned, "published": result.published, "plan_version": result.plan_version,
               "forced": result.forced, "reason": result.reason, "runtime_s": round(result.runtime_s, 4)}
        if extra:
            rec.update(extra)
        self.audit.append(rec)
        if self.write_outputs:
            with open(self._events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")

    def _set_state(self, rid: str, new: DispatchState, *, authoritative: bool = False) -> None:
        old = self.job_states.get(rid, DispatchState.UNASSIGNED)
        if old == new:
            return
        if new not in ALLOWED_TRANSITIONS[old]:
            raise ValueError(f"illegal transition {old.value} -> {new.value} for {rid}")
        if old in LOCKED_STATES and new == DispatchState.UNASSIGNED and not authoritative:
            raise ValueError(f"{rid}: releasing a locked job requires an authoritative staff/Crew Portal event")
        self.job_states[rid] = new

    # ------------------------------------------------------------------ event entry point
    def handle(self, event: dict) -> ControllerResult:
        if event["time"].tzinfo is None:
            raise ValueError("event time must be timezone-aware")
        handler = getattr(self, "_on_" + event["type"].lower(), None)
        if handler is None:
            raise ValueError(f"unknown event type {event['type']}")
        res = handler(event)
        self._log(event, res)
        return res

    # ------------------------------------------------------------------ handlers
    def _on_queue_updated(self, ev) -> ControllerResult:
        queue = ev["queue"]
        issues = validate_c11_output(queue)
        if issues:
            res = ControllerResult("QUEUE_UPDATED", reason="C11 contract violations: " + "; ".join(issues[:5]))
            # still accept the queue (positions are used as given), but record the violation
        old = self.queue_index
        new = {r["request_id"]: r for r in queue}
        planned = self.plan.assignment() if self.plan else {}
        new_eligible, removed, became_ineligible, rank_changed = [], [], [], 0
        for rid, r in new.items():
            st = r.get("c11_queue_status")
            prev = old.get(rid)
            state = self.job_states.get(rid)
            if state in LOCKED_STATES or state in (DispatchState.COMPLETED, DispatchState.CANCELLED):
                continue   # cancellation/completion is final for Dispatch; a reopened case needs a new request_id
            if st == "hold":
                self._set_state(rid, DispatchState.HELD)
            elif st == "provisional_review":
                self._set_state(rid, DispatchState.PENDING_REVIEW)
            elif state in (None, DispatchState.HELD, DispatchState.PENDING_REVIEW):
                self._set_state(rid, DispatchState.UNASSIGNED)
            if st == "provisional" and (prev is None or prev.get("c11_queue_status") != "provisional"):
                new_eligible.append(rid)
            if st != "provisional" and rid in planned:
                became_ineligible.append(rid)
            if prev is not None and prev.get("overall_queue_position") != r.get("overall_queue_position"):
                rank_changed += 1
        for rid in old:
            if rid not in new:
                state = self.job_states.get(rid)
                if state in LOCKED_STATES:
                    continue   # reported as locked conflict by the engine; never auto-redirect
                if state not in (DispatchState.COMPLETED, DispatchState.CANCELLED):
                    self._set_state(rid, DispatchState.CANCELLED)
                    removed.append(rid)
        self.queue, self.queue_index = queue, new
        forced = []
        if any(r in planned for r in removed) or became_ineligible:
            forced.append("planned request cancelled/held/sent to review")
        if new_eligible:
            self.dirty.add("new eligible request")
            if self.cfg["dynamic"]["replan_policy"] in ("event", "hybrid") and self._outranks_planned(new_eligible):
                forced.append("new eligible request outranks planned work")
        if rank_changed:
            self.dirty.add("C11 rank change")
        for f in forced:
            self.forced_reasons.add(f)
        if self.plan is None and any(r.get("c11_queue_status") == "provisional" for r in queue):
            self.forced_reasons.add("initial plan")
        res = self._maybe_replan(ev, f"queue update ({ev.get('trigger')}): +{len(new_eligible)} eligible, "
                                     f"{len(removed)} removed, {rank_changed} rank changes")
        if issues:
            res.reason += " | C11 contract violations: " + "; ".join(issues[:3])
        return res

    def _outranks_planned(self, rids) -> bool:
        if not self.plan:
            return True
        worst = {}
        for cid, stops in self.plan.routes.items():
            for s in stops:
                ag = self.crews[cid].agency
                worst[ag] = max(worst.get(ag, 0), s["overall_queue_position"])
        for rid in rids:
            r = self.queue_index[rid]
            pos = r.get("overall_queue_position")
            if pos is not None and pos < worst.get(r.get("agency"), 0):
                return True
            if r.get("agency") not in worst:      # agency had nothing planned: idle capacity may exist
                return True
        return False

    def awaiting_status_updates(self, now) -> list:
        """Locked jobs whose ESTIMATED finish/arrival has passed without a Crew Portal update.

        They stay in their reported state (never auto-completed); staff can follow up.
        """
        out = []
        for cid, st in self.crew_status.items():
            for j in st.locked:
                if j.state == DispatchState.IN_PROGRESS and j.since and minutes_between(j.since, now) > j.service_min:
                    out.append({"request_id": j.request_id, "crew_id": cid, "state": j.state.value,
                                "overdue_min": round(minutes_between(j.since, now) - j.service_min, 1)})
                elif j.state == DispatchState.EN_ROUTE and j.since and minutes_between(j.since, now) > j.est_travel_min + j.service_min:
                    out.append({"request_id": j.request_id, "crew_id": cid, "state": j.state.value,
                                "overdue_min": round(minutes_between(j.since, now) - j.est_travel_min - j.service_min, 1)})
        return out

    def _on_tick(self, ev) -> ControllerResult:
        res = self._maybe_replan(ev, "periodic check")
        overdue = self.awaiting_status_updates(ev["time"])
        if overdue:
            res.reason += f" | {len(overdue)} job(s) awaiting Crew Portal status update: " +                 ", ".join(f"{o['request_id']}({o['state']}, +{o['overdue_min']:g} min)" for o in overdue)
        return res

    def _on_job_status(self, ev) -> ControllerResult:
        rid, cid, state = ev["request_id"], ev["crew_id"], DispatchState(ev["state"])
        st = self.crew_status[cid]
        now = ev["time"]
        rec = self.queue_index.get(rid, {})
        if state == DispatchState.EN_ROUTE:
            job = next((j for j in st.locked if j.request_id == rid), None)
            if job is None:
                raise ValueError(f"{rid}: EN_ROUTE without approval (APPROVED_NOT_STARTED) is rejected")
            self._set_state(rid, DispatchState.EN_ROUTE)
            job.state, job.since = DispatchState.EN_ROUTE, now
            if ev.get("est_travel_min") is not None:
                job.est_travel_min = float(ev["est_travel_min"])
            return ControllerResult("JOB_STATUS", reason=f"{rid} en route by {cid} (locked)")
        if state == DispatchState.IN_PROGRESS:
            job = next(j for j in st.locked if j.request_id == rid)
            self._set_state(rid, DispatchState.IN_PROGRESS)
            job.state, job.since = DispatchState.IN_PROGRESS, now
            st.lat, st.lon = job.lat, job.lon
            return ControllerResult("JOB_STATUS", reason=f"{rid} work started by {cid} (locked)")
        if state == DispatchState.COMPLETED:
            job = next((j for j in st.locked if j.request_id == rid), None)
            if job is None or self.job_states.get(rid) != DispatchState.IN_PROGRESS:
                raise ValueError(f"{rid}: COMPLETED is only accepted for a job IN_PROGRESS with crew {cid}")
            self._set_state(rid, DispatchState.COMPLETED)
            st.locked.remove(job)
            st.worked_min += float(ev.get("actual_travel_min", job.est_travel_min)) + float(ev.get("actual_service_min", job.service_min))
            st.lat, st.lon = job.lat, job.lon
            planned_finish = minutes_between(job.since, now) if job.since else 0
            drift = abs(planned_finish - job.service_min)
            self.dirty.add("job completed")
            if drift >= float(self.cfg["dynamic"].get("overdue_buffer_min", 15)):
                self.forced_reasons.add(f"completion time deviated {drift:.0f} min from estimate")
            return self._maybe_replan(ev, f"{rid} completed by {cid}")
        if state == DispatchState.CANCELLED:
            self._set_state(rid, DispatchState.CANCELLED)
            st.locked = [j for j in st.locked if j.request_id != rid]
            self.forced_reasons.add("job cancelled by authoritative source")
            return self._maybe_replan(ev, f"{rid} cancelled")
        raise ValueError(f"unsupported JOB_STATUS state {state}")

    def _on_approval(self, ev) -> ControllerResult:
        rid, cid = ev["request_id"], ev["crew_id"]
        if not ev.get("verified"):
            return ControllerResult("APPROVAL", reason=f"rejected: approval for {rid} is not verified")
        planned = self.plan.assignment() if self.plan else {}
        if planned.get(rid) != cid:
            return ControllerResult("APPROVAL", reason=f"rejected: {rid} is not planned for {cid} in the current plan")
        self._set_state(rid, DispatchState.APPROVED_NOT_STARTED)
        rec = self.queue_index[rid]
        pr = self.plan.requests[rid]
        stop = next(s for s in self.plan.routes[cid] if s["request_id"] == rid)
        self.crew_status[cid].locked.append(LockedJob(request_id=rid, crew_id=cid, state=DispatchState.APPROVED_NOT_STARTED,
                                                      lat=pr.lat, lon=pr.lon, service_min=pr.service_min,
                                                      est_travel_min=stop["travel_in_min"], since=ev["time"],
                                                      approval_ref=ev.get("approval_ref") or ev.get("approver")))
        self.job_crew[rid] = cid
        # Remove it from the current plan's future work so the next proposal starts after it.
        self.plan.routes[cid] = [s for s in self.plan.routes[cid] if s["request_id"] != rid]
        return ControllerResult("APPROVAL", reason=f"{rid} approved for {cid} by {ev.get('approver')} (locked)")

    def _on_crew_status(self, ev) -> ControllerResult:
        cid = ev["crew_id"]
        st = self.crew_status[cid]
        was = st.available
        st.available = bool(ev["available"])
        if ev.get("lat") is not None:
            st.lat, st.lon = ev["lat"], ev["lon"]
        if was and not st.available:
            st.note = ev.get("note", "unavailable")
            if self.plan and self.plan.routes.get(cid):
                self.forced_reasons.add(f"crew {cid} unavailable with planned work")
            else:
                self.dirty.add(f"crew {cid} unavailable")
        elif st.available and not was:
            self.forced_reasons.add(f"crew {cid} available again")
        return self._maybe_replan(ev, f"crew {cid} available={st.available}")

    def _on_staff_override(self, ev) -> ControllerResult:
        rid = ev["request_id"]
        if ev.get("action") != "release_lock" or not ev.get("approver"):
            return ControllerResult("STAFF_OVERRIDE", reason="rejected: unsupported action or missing approver")
        for st in self.crew_status.values():
            st.locked = [j for j in st.locked if j.request_id != rid]
        self._set_state(rid, DispatchState.UNASSIGNED, authoritative=True)
        self.forced_reasons.add(f"staff released locked job {rid}")
        return self._maybe_replan(ev, f"staff override released {rid}")

    # ------------------------------------------------------------------ replanning
    def _due(self, now) -> bool:
        if self.last_replan is None:
            return True
        return minutes_between(self.last_replan, now) >= float(self.cfg["dynamic"]["replan_interval_min"]) - 1e-9

    def _maybe_replan(self, ev, why: str) -> ControllerResult:
        now = ev["time"]
        policy = self.cfg["dynamic"]["replan_policy"]
        forced = bool(self.forced_reasons)
        if not forced:
            if not self.dirty:
                return ControllerResult(ev["type"], reason=f"{why}; no material change - no replan")
            if policy == "periodic" or policy == "hybrid":
                if ev["type"] != "TICK" or not self._due(now):
                    return ControllerResult(ev["type"], reason=f"{why}; change deferred to next periodic replan")
            elif policy == "event":
                if self.last_replan and minutes_between(self.last_replan, now) < float(self.cfg["dynamic"]["debounce_min"]):
                    return ControllerResult(ev["type"], reason=f"{why}; debounced")
        if not self.queue:
            return ControllerResult(ev["type"], reason=f"{why}; no queue yet")
        return self._replan(ev, why, forced)

    def snapshot(self, now) -> Snapshot:
        prev_assign = self.plan.assignment() if self.plan else {}
        prev_routes = self.plan.sequences() if self.plan else {}
        return Snapshot(now=now, queue=self.queue, crews=list(self.crews.values()), crew_status=self.crew_status,
                        job_states=dict(self.job_states), previous_assignment=prev_assign, previous_routes=prev_routes)

    def _replan(self, ev, why, forced) -> ControllerResult:
        t0 = time.perf_counter()
        now = ev["time"]
        snap = self.snapshot(now)
        method = self.method if self.plan is None else self.replan_method
        overrides = None if self.plan is None else self.ga_replan_overrides
        new = optimize_plan(snap, self.router, self.cfg, self.catalog, seed=self.seed, method=method,
                            ga_overrides=overrides, keep_instances=True, reconstruct_paths=False)
        # Locked-commitment guard: no locked/completed job may appear in proposed future work.
        for cid, stops in new.routes.items():
            for s in stops:
                if self.job_states.get(s["request_id"]) in LOCKED_STATES | {DispatchState.COMPLETED}:
                    self.locked_violations += 1
        f_new, b_new, sec_new = self._objective_parts([sol for sol in new.solutions.values()])
        if self.plan is not None:
            f_old, b_old, sec_old = self._objective_parts(self._carry_forward(new))
        else:
            f_old, b_old, sec_old = float("inf"), float("inf"), float("inf")
        improvement = f_old - f_new
        # Publish a non-forced replan only if it is better by a margin: any reduction of the
        # rank-weighted backlog, or a secondary-objective (travel/delay/stability) gain of at least
        # max(abs, rel * secondary). Works for weighted-sum and lexicographic objectives alike.
        thresh = max(float(self.cfg["dynamic"]["min_improvement_abs"]),
                     float(self.cfg["dynamic"]["min_improvement_rel"]) * abs(sec_old) if sec_old != float("inf") else 0.0)
        better = improvement > 1e-9 and ((b_old - b_new) > 1e-9 or (sec_old - sec_new) >= thresh)
        publish = forced or self.plan is None or better
        if publish and self.plan is not None:
            from ..outputs import plan_changes
            pre = plan_changes(self.plan.assignment(), self.plan.sequences(), new.assignment(), new.sequences())
            if not pre["material_change"]:
                publish = False
                why += "; replan reproduced the current plan"
        self.last_replan = now
        reason = (f"{why}; backlog_w {b_old:.3f}->{b_new:.3f}, secondary {sec_old:.1f}->{sec_new:.1f} "
                  f"(threshold {thresh:.1f})")
        log = {"time": now, "forced": forced, "forced_reasons": sorted(self.forced_reasons), "dirty": sorted(self.dirty),
               "f_prev": f_old, "f_new": f_new, "backlog_prev": b_old, "backlog_new": b_new, "secondary_prev": sec_old,
               "secondary_new": sec_new, "published": publish, "runtime_s": time.perf_counter() - t0,
               "method": method}
        self.forced_reasons.clear()
        self.dirty.clear()
        res = ControllerResult(ev["type"], replanned=True, forced=forced, runtime_s=time.perf_counter() - t0)
        if not publish:
            res.reason = (f"{why}; backlog_w {b_old:.3f}->{b_new:.3f}, secondary {sec_old:.1f}->{sec_new:.1f} "
                          f"(threshold {thresh:.1f}) -> kept previous plan (no material change or improvement below threshold)")
            log["changes"] = None
            self.replan_log.append(log)
            return res
        from ..engine import _attach_paths  # final-plan path reconstruction only
        crew_nodes = {}
        for cid, cp in new.crews.items():
            if cp.usable:
                sn = self.router.snap([cp.origin_lat, self.crews[cid].depot_lat], [cp.origin_lon, self.crews[cid].depot_lon])
                crew_nodes[cid] = (int(sn.node[0]), int(sn.node[1]))
        req_node = {}
        ids = [s["request_id"] for st in new.routes.values() for s in st]
        if ids:
            sn = self.router.snap([new.requests[r].lat for r in ids], [new.requests[r].lon for r in ids])
            req_node = {r: int(n) for r, n in zip(ids, sn.node)}
        _attach_paths(new.routes, new.crews, crew_nodes, req_node, self.router, self.crews)
        self.version += 1
        for rid in list(self.job_states):
            if self.job_states[rid] == DispatchState.PLANNED:
                self._set_state(rid, DispatchState.UNASSIGNED)
        for rid in new.assignment():
            self._set_state(rid, DispatchState.PLANNED)
        self.prev_doc, self.prev_geo = self.doc, self.geo
        geo_name = f"routes_v{self.version:04d}.geojson"
        event_meta = {"type": ev["type"], "time": now, "trigger": ev.get("trigger"), "source": ev.get("source"),
                      "simulated": bool(ev.get("simulated", False)), "why": why, "forced": forced,
                      "forced_reasons": log["forced_reasons"]}
        self.plan = new
        self.doc = build_plan_document(new, snap, version=self.version, run_id=self.run_id, event=event_meta, cfg=self.cfg,
                                       data_versions=self.data_versions, c11_info=self.c11_info, prev_doc=self.prev_doc,
                                       job_states=self.job_states, crews=self.crews, geometry_file=geo_name,
                                       extra_diagnostics={"replan": {k: v for k, v in log.items() if k != "time"}})
        self.geo = route_geojson(new, self.router, self.version)
        if self.write_outputs:
            write_new(self.run_dir / "plans" / f"plan_v{self.version:04d}.json", dumps(self.doc))
            write_new(self.run_dir / "plans" / geo_name, dumps(self.geo))
        ch = self.doc["changes_from_previous"]
        log["changes"] = {k: (len(v) if isinstance(v, list) else v) for k, v in ch.items()}
        log["version"] = self.version
        self.replan_log.append(log)
        res.published, res.plan_version, res.changes = True, self.version, ch
        res.reason = reason + " -> published"
        res.runtime_s = time.perf_counter() - t0
        if self.maps and self.write_outputs and (ch["material_change"] or self.version == 1):
            res.map_paths = self._render_maps()
        return res

    def _carry_forward(self, new) -> list:
        """The previous plan's unlocked routes, re-evaluated on the new snapshot."""
        from ..engine import _warm_start
        prev_routes = self.plan.sequences()
        return [_warm_start(inst, prev_routes) for inst in new.instances.values()]

    @staticmethod
    def _objective_parts(solutions) -> tuple:
        """(F, rank-weighted backlog, secondary = F - lambda_B * backlog)."""
        f = b = sec = 0.0
        for sol in solutions:
            c = sol.components()
            fb = sol.inst.lambda_backlog * c["backlog_weight"]
            f += sol.fitness()
            b += c["backlog_weight"]
            sec += sol.fitness() - fb
        return f, b, sec

    def _render_maps(self) -> list:
        """Maps only for agencies whose plan materially changed; capped by maps.max_maps_per_run (files)."""
        from ..viz.maps import BaseMap, render_comparison, render_plan_map
        cap = int(self.cfg["maps"].get("max_maps_per_run", 40))
        if self.maps_written >= cap:
            return []
        if self.base_map is None:
            self.base_map = BaseMap(self.router.net)
        ch = self.doc["changes_from_previous"]
        rq = {r["request"]["request_id"]: r["request"].get("agency") for r in self.doc["requests"]}
        touched = {rq.get(r) for k in ("newly_assigned", "no_longer_assigned", "moved_to_other_crew") for r in ch[k]}
        touched |= {self.crews[c].agency for c in ch["resequenced_crews"]}
        active = {c["agency"] for c in self.doc["crews"] if c["planned_stops"] or c["locked_commitments"]}
        agencies = sorted(active if self.version == 1 else (touched & (active | touched)) - {None})
        out = []
        mdir = self.run_dir / "maps"
        for ag in agencies:
            if self.maps_written >= cap:
                break
            p = render_plan_map(self.doc, self.geo, self.router.net, mdir / f"plan_v{self.version:04d}_{ag}.png",
                                agency=ag, base=self.base_map, dpi=int(self.cfg["maps"].get("dpi", 170)))
            out.append(str(p))
            self.maps_written += 1
            if self.prev_doc is not None and self.maps_written < cap:
                p = render_comparison(self.prev_doc, self.prev_geo, self.doc, self.geo, self.router.net,
                                      mdir / f"compare_v{self.version - 1:04d}_v{self.version:04d}_{ag}.png", agency=ag, base=self.base_map)
                out.append(str(p))
                self.maps_written += 1
        return out
