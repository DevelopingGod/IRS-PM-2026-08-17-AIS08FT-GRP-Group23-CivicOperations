"""Deterministic discrete-event replay of a request stream (RESEARCH SIMULATION ONLY).

Real inputs: request arrival times, locations, types (historical NYC311 windows) or fixture data.
Simulated (and labelled ``simulated: true`` in every event): staff approvals at departure, Crew
Portal status updates, actual travel/service durations (lognormal noise around the estimates), crew
breakdowns and human-review completions. Nothing simulated is presented as a real Crew Portal update.

The simulator plays the role of the CivicOps pipeline around Dispatch: it keeps the set of open
requests, calls the C11 provider whenever requests arrive (coalesced) and on every periodic tick,
and forwards the C11 output to the controller exactly as the live orchestrator would.
"""
from __future__ import annotations

import heapq
import itertools
import math
from datetime import datetime, timedelta

import numpy as np

from ..optimize.weights import rank_weights
from ..schemas import DispatchState, add_minutes, minutes_between


def lognormal_minutes(rng, mean: float, cv: float) -> float:
    if mean <= 0 or cv <= 0:
        return max(0.0, mean)
    s2 = math.log(1 + cv * cv)
    return float(rng.lognormal(math.log(mean) - s2 / 2, math.sqrt(s2)))


class ReplaySimulator:
    def __init__(self, controller, provider, arrivals: list, cfg: dict, seed: int = 0, scenario_events=(),
                 tick_min: float | None = None):
        self.ctl = controller
        self.provider = provider
        self.cfg = cfg
        self.rng = np.random.default_rng(seed)
        self.arrivals = sorted(arrivals, key=lambda r: (r["created_date"], r["request_id"]))
        self.scenario_events = list(scenario_events)
        self.tick_min = float(tick_min or cfg["dynamic"]["replan_interval_min"])
        self.open: dict = {}
        self.heap: list = []
        self._seq = itertools.count()
        self.refresh_pending = False
        self.first_rank: dict = {}
        self.first_seen: dict = {}
        self.started: dict = {}
        self.completed: dict = {}
        self.actual_travel = 0.0
        self.actual_work: dict = {}
        self.crew_job: dict = {}
        self.controller_results: list = []
        self.c11_calls = 0
        self.c11_seconds = 0.0
        self.resolved_scenarios: list = []

    def push(self, t, kind, payload=None):
        heapq.heappush(self.heap, (t, next(self._seq), kind, payload))

    # ------------------------------------------------------------------ C11 refresh
    def refresh(self, now, trigger):
        import time as _t
        t0 = _t.perf_counter()
        queue = self.provider.prioritize(list(self.open.values()), now) if self.open else []
        self.c11_calls += 1
        self.c11_seconds += _t.perf_counter() - t0
        for r in queue:
            rid = r["request_id"]
            if rid not in self.first_rank and r.get("overall_queue_position"):
                self.first_rank[rid] = int(r["overall_queue_position"])
        self.deliver({"type": "QUEUE_UPDATED", "time": now, "queue": queue, "trigger": trigger,
                      "source": self.provider.info["provider"], "simulated": False})

    def deliver(self, ev):
        res = self.ctl.handle(ev)
        self.controller_results.append(res)
        return res

    # ------------------------------------------------------------------ crew execution
    def dispatch_idle(self, now):
        if self.ctl.plan is None:
            return
        for cid, st in self.ctl.crew_status.items():
            if not st.available or st.locked or cid in self.crew_job:
                continue
            if st.duty_start and now < st.duty_start or st.duty_end and now >= st.duty_end:
                continue
            stops = self.ctl.plan.routes.get(cid) or []
            if not stops:
                continue
            s = stops[0]
            rid = s["request_id"]
            if self.ctl.job_states.get(rid) != DispatchState.PLANNED:
                continue
            ev = {"type": "APPROVAL", "time": now, "request_id": rid, "crew_id": cid, "verified": True,
                  "approver": "SIMULATED_STAFF_APPROVER", "approval_ref": f"SIM-APPROVAL-{rid}",
                  "source": "simulated_staff_portal", "simulated": True}
            self.deliver(ev)
            if self.ctl.job_states.get(rid) != DispatchState.APPROVED_NOT_STARTED:
                continue
            est = float(s["travel_in_min"])
            self.deliver({"type": "JOB_STATUS", "time": now, "request_id": rid, "crew_id": cid, "state": "EN_ROUTE",
                          "est_travel_min": est, "source": "simulated_crew_portal", "simulated": True})
            actual = lognormal_minutes(self.rng, est, float(self.cfg["simulation"]["travel_time_cv"]))
            token = (cid, rid, next(self._seq))
            self.crew_job[cid] = token
            self.push(add_minutes(now, actual), "arrive", {"token": token, "travel": actual, "service_est": s["service_min"]})

    # ------------------------------------------------------------------ main loop
    def run(self, start: datetime, end: datetime) -> dict:
        self.ctl.start_duty(start)
        for r in self.arrivals:
            self.push(max(r["created_date"], start), "arrival", r)
        t = start
        while t <= end:
            self.push(t, "tick")
            t = add_minutes(t, self.tick_min)
        for ds in sorted({st.duty_start for st in self.ctl.crew_status.values() if st.duty_start and st.duty_start > start}):
            self.push(ds, "period_start")
        for ev in self.scenario_events:
            self.push(ev["time"], "scenario", ev)
        while self.heap:
            now, _, kind, payload = heapq.heappop(self.heap)
            if now > end:
                break
            if kind == "arrival":
                rid = payload["request_id"]
                self.open[rid] = dict(payload)
                self.first_seen[rid] = now
                if not self.refresh_pending:
                    self.refresh_pending = True
                    self.push(add_minutes(now, float(self.cfg["dynamic"]["debounce_min"])), "refresh")
            elif kind == "refresh":
                self.refresh_pending = False
                self.refresh(now, "new_request")
            elif kind == "tick":
                if self.open:
                    self.refresh(now, "periodic")
                self.deliver({"type": "TICK", "time": now, "source": "replay_clock", "simulated": True})
            elif kind == "period_start":
                self.ctl.forced_reasons.add("service period start (operating hours)")
                if self.open:
                    self.refresh(now, "period_start")
            elif kind == "arrive":
                cid, rid, _ = payload["token"]
                if self.crew_job.get(cid) != payload["token"] or self.ctl.job_states.get(rid) != DispatchState.EN_ROUTE:
                    continue   # job was released (e.g. breakdown + staff override)
                self.actual_travel += payload["travel"]
                self.started[rid] = now
                self.deliver({"type": "JOB_STATUS", "time": now, "request_id": rid, "crew_id": cid, "state": "IN_PROGRESS",
                              "source": "simulated_crew_portal", "simulated": True})
                svc = lognormal_minutes(self.rng, float(payload["service_est"]), float(self.cfg["simulation"]["service_time_cv"]))
                self.push(add_minutes(now, svc), "finish", {**payload, "service": svc})
            elif kind == "finish":
                cid, rid, _ = payload["token"]
                if self.crew_job.get(cid) != payload["token"]:
                    continue
                del self.crew_job[cid]
                self.completed[rid] = now
                self.actual_work[cid] = self.actual_work.get(cid, 0.0) + payload["travel"] + payload["service"]
                self.open.pop(rid, None)
                self.deliver({"type": "JOB_STATUS", "time": now, "request_id": rid, "crew_id": cid, "state": "COMPLETED",
                              "actual_travel_min": payload["travel"], "actual_service_min": payload["service"],
                              "source": "simulated_crew_portal", "simulated": True})
            elif kind == "scenario":
                self.apply_scenario(payload, now)
            self.dispatch_idle(now)
        return self.summary(start, end)

    def apply_scenario(self, ev, now):
        ev = dict(ev)
        planned = self.ctl.plan.assignment() if self.ctl.plan else {}
        if ev.get("crew_id") == "AUTO_BUSIEST" and self.ctl.plan:      # demo helper: crew with most planned stops
            busy = sorted(self.ctl.plan.routes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
            ev["crew_id"] = busy[0][0]
        if ev.get("request_id") == "AUTO_PLANNED":                      # demo helper: a planned, not-yet-locked request
            cands = sorted(r for r in planned if self.ctl.job_states.get(r) == DispatchState.PLANNED)
            if not cands:
                return
            ev["request_id"] = cands[-1]
        self.resolved_scenarios.append({**ev, "time": now})
        if ev["type"] == "CREW_STATUS":
            cid = ev["crew_id"]
            self.deliver({**ev, "time": now, "simulated": True, "source": "simulated_scenario"})
            st = self.ctl.crew_status[cid]
            enroute = [j for j in st.locked if j.state in (DispatchState.EN_ROUTE, DispatchState.APPROVED_NOT_STARTED)]
            for j in enroute:   # a broken-down vehicle cannot reach the job: simulated staff releases it
                self.crew_job.pop(cid, None)
                self.deliver({"type": "STAFF_OVERRIDE", "time": now, "request_id": j.request_id, "action": "release_lock",
                              "approver": "SIMULATED_STAFF_APPROVER", "source": "simulated_staff_portal", "simulated": True})
        elif ev["type"] == "REVIEW_COMPLETED":
            rid = ev["request_id"]
            if rid in self.open:
                self.open[rid]["c11_queue_status"] = ev.get("new_status", "provisional")
                self.open[rid]["review_outcome_simulated"] = True
                self.refresh(now, "review_completed")
        elif ev["type"] == "CANCEL":
            rid = ev["request_id"]
            self.open.pop(rid, None)
            self.refresh(now, "cancellation")

    # ------------------------------------------------------------------ metrics
    def summary(self, start, end) -> dict:
        wcfg = self.cfg["objective"]["rank_weight"]
        rids = [r for r in self.first_rank]
        w = dict(zip(rids, rank_weights([self.first_rank[r] for r in rids], wcfg["transform"], float(wcfg.get("param", 25)),
                                        max(self.first_rank.values()) if rids else 1))) if rids else {}
        done = [r for r in rids if r in self.completed]
        waits = [(w[r], minutes_between(self.first_seen[r], self.started[r])) for r in rids if r in self.started]
        budgets = {cid: c.work_time_budget_min for cid, c in self.ctl.crews.items()}
        overtime = sum(max(0.0, self.actual_work.get(cid, 0.0) - budgets[cid]) for cid in budgets)
        pubs = [l for l in self.ctl.replan_log if l.get("published")]
        changed = [l["changes"]["changed_assignment_count"] for l in pubs if l.get("changes")]
        awaiting = sum(len(st.locked) for st in self.ctl.crew_status.values())
        return {
            "start": start.isoformat(), "end": end.isoformat(), "requests_arrived": len(self.first_seen),
            "requests_ranked": len(rids), "completed_simulated": len(done),
            "completed_pct": 100.0 * len(done) / len(rids) if rids else 0.0,
            "weighted_completed_share": sum(w[r] for r in done) / sum(w.values()) if w else 0.0,
            "weighted_mean_wait_to_start_min": (sum(a * b for a, b in waits) / sum(a for a, _ in waits)) if waits else None,
            "actual_travel_min": self.actual_travel, "overtime_min": overtime,
            "replans": len(self.ctl.replan_log), "plans_published": len(pubs),
            "mean_changed_assignments_per_publish": float(np.mean(changed)) if changed else 0.0,
            "total_changed_assignments": int(sum(changed)) if changed else 0,
            "optimiser_seconds": float(sum(l["runtime_s"] for l in self.ctl.replan_log)),
            "c11_calls": self.c11_calls, "c11_seconds": self.c11_seconds,
            "locked_commitment_violations": self.ctl.locked_violations,
            "jobs_still_locked_at_end": awaiting,
        }
