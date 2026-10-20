"""Crew deployment and route optimisation, Mohammad's component, over the whole ranked queue.

Like Prioritisation it plans every open request together, so it runs on the queue rather than
on one request. The adapter only builds his input from shared fields and reads his plan back:

* the queue Prioritisation produced, passed with its own queue status and position (his only
  priority input), plus each request's skill, coordinates and the catalog's service minutes;
* the crews, from the shared crew table, with their depots;
* what is already decided: a job city staff approved is locked to its crew and never planned
  again, and minutes a crew already worked today count against its day.

The plan is a proposal. Nothing is assigned until a person approves it in the staff portal.
If the component is switched off or fails, the agreed safe default applies: no plan, staff
assign crews by hand and each crew sees its jobs in queue order.

Planning takes a few seconds, so the latest plan is kept and a new one is made in the
background whenever the queue, the approvals or the time of day have moved on.
"""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from . import data, db, switches
from .plugins.base import ERROR, LIVE, OFF, Call

NAME = "Crew deployment and routing"
HOME = Path(__file__).resolve().parents[2] / "components" / "crew_deployment_and_routing"
REPLAN_AFTER = timedelta(minutes=15)       # the component's own replanning interval
if str(HOME / "src") not in sys.path:
    sys.path.insert(0, str(HOME / "src"))

_lock = threading.Lock()
_state: dict = {"key": None, "plan": None, "made_at": None, "running": False, "call": None}


# ------------------------------------------------------------------ building his input
def _locked() -> dict[str, list[dict]]:
    """Approved, still open jobs per crew, in the order staff approved them."""
    with db.conn() as c:
        rows = c.execute("select request_id, crew_id, assigned_at from crew_jobs "
                         "where outcome is null order by assigned_at").fetchall()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["crew_id"], []).append(dict(r))
    return out


def _worked_today(now: datetime) -> dict[str, float]:
    """Service minutes of the jobs each crew closed today, from the catalog."""
    day = now.strftime("%Y-%m-%d")
    with db.conn() as c:
        rows = c.execute("select j.crew_id, r.data from crew_jobs j join requests r on r.id = j.request_id "
                         "where j.closed_at like ?", (f"{day}%",)).fetchall()
    worked: dict[str, float] = {}
    for r in rows:
        worked[r["crew_id"]] = worked.get(r["crew_id"], 0.0) + _service_min(json.loads(r["data"]))
    return worked


def _positions(now: datetime) -> dict[str, tuple[float, float]]:
    """Where each crew is now: the place of the last job it closed today. Before that, its depot."""
    day = now.strftime("%Y-%m-%d")
    with db.conn() as c:
        rows = c.execute("select j.crew_id, r.data from crew_jobs j join requests r on r.id = j.request_id "
                         "where j.closed_at like ? order by j.closed_at", (f"{day}%",)).fetchall()
    out: dict[str, tuple[float, float]] = {}
    for r in rows:
        s = (json.loads(r["data"]).get("slots") or {})
        if s.get("latitude") is not None:
            out[r["crew_id"]] = (float(s["latitude"]), float(s["longitude"]))
    return out


def _service_min(q: dict) -> float:
    try:
        return float(data.catalog().get(q.get("request_type") or "", {}).get("est_service_minutes") or 45)
    except ValueError:
        return 45.0


def _inputs(items: list[dict], now: datetime) -> tuple[list[dict], list[dict], dict, dict]:
    docs = {q["request_id"]: db.load_json("requests", q["request_id"]) or {} for q in items}
    records = []
    for q in items:
        d = docs[q["request_id"]]
        s = d.get("slots") or {}
        records.append({
            "request_id": q["request_id"], "agency": q.get("agency"),
            # The fallback queue has no status of its own: every ranked request is plannable then.
            # A job staff pulled back from a crew is held, so it is not planned again by itself.
            "c11_queue_status": "hold" if d.get("dispatch_hold") else
                                q.get("queue_status") or ("hold" if q.get("on_hold") else "provisional"),
            "overall_queue_position": None if q.get("on_hold") else q.get("queue_position"),
            "required_skills": d.get("required_skills") or [],
            "latitude": s.get("latitude"), "longitude": s.get("longitude"),
            "request_type_code": d.get("request_type"), "est_service_minutes": _service_min(d)})
    crews = [{"crew_id": c["crew_id"], "agency": c["agency"], "skills": c["skills"],
              "depot_lat": c["depot_lat"], "depot_lon": c["depot_lon"]}
             for c in data.crews().values() if c.get("depot_lat") is not None]
    from crew_dispatch.schemas import CrewStatus, DispatchState, LockedJob
    by_id = {r["request_id"]: r for r in records}
    status, states = {}, {}
    worked = _worked_today(now)
    locked = _locked()
    where = _positions(now)
    for crew_id in set(locked) | set(worked) | set(where):
        jobs = []
        for j in locked.get(crew_id, []):
            r = by_id.get(j["request_id"])
            if not r or r["latitude"] is None:
                continue
            jobs.append(LockedJob(j["request_id"], crew_id, DispatchState.APPROVED_NOT_STARTED, r["latitude"],
                                  r["longitude"], r["est_service_minutes"], approval_ref=j["assigned_at"]))
            states[j["request_id"]] = DispatchState.APPROVED_NOT_STARTED
        lat, lon = where.get(crew_id, (None, None))     # plans start from where the crew is now
        status[crew_id] = CrewStatus(crew_id, lat=lat, lon=lon, worked_min=worked.get(crew_id, 0.0), locked=jobs)
    return records, crews, status, states


# ------------------------------------------------------------------ reading his plan back
def _summary(doc: dict, items: list[dict], ms: float) -> dict:
    """The plan in the shape the portals show: each crew's route with names, times and the
    road it drives, the reasons a request is not planned, and the headline numbers."""
    info = {}
    for q in items:
        s = q.get("slots") or {}
        info[q["request_id"]] = {"label": q.get("label"), "urgency": q.get("urgency_level"),
                                 "address": s.get("incident_address"), "borough": s.get("borough"),
                                 "lat": s.get("latitude"), "lon": s.get("longitude"),
                                 "queue_position": q.get("queue_position")}
    roads: dict[str, list] = {}
    for f in (doc.get("route_geometry") or {}).get("features", []):
        p = f.get("properties") or {}
        coords = (f.get("geometry") or {}).get("coordinates") or []
        if coords:
            roads.setdefault(p.get("crew_id"), []).append({"kind": p.get("kind"), "to": p.get("to_request"),
                                                          "points": [[round(x, 5), round(y, 5)] for x, y in coords[::2] + coords[-1:]]})
    crews = []
    for c in doc.get("crews") or []:
        locked = [{"request_id": j if isinstance(j, str) else j.get("request_id"), "approved": True}
                  for j in c.get("locked_commitments") or []]
        planned = [{**info.get(s["request_id"], {}), "request_id": s["request_id"], "sequence": s.get("sequence"),
                    "est_start": s.get("est_start"), "est_finish": s.get("est_finish"),
                    "travel_min": round(s.get("travel_in_min") or 0, 1), "service_min": s.get("service_min"),
                    "distance_km": round((s.get("leg_distance_m") or 0) / 1000, 1), "approved": False}
                   for s in c.get("planned_stops") or []]
        if not planned and not locked:
            continue
        crews.append({"crew_id": c["crew_id"], "agency": c.get("agency"), "depot": c.get("depot"),
                      "position": c.get("current_position") or c.get("depot"),
                      "available_at": c.get("available_at"), "duty_end": c.get("duty_end"),
                      "travel_min": round(c.get("planned_travel_min") or 0, 1),
                      "work_min": round(c.get("planned_work_min") or 0, 1),
                      "remaining_min": round(c.get("remaining_after_plan_min") or 0, 1),
                      "approved": [{**info.get(j["request_id"], {}), **j} for j in locked],
                      "stops": planned, "roads": roads.get(c["crew_id"], [])})
    crews.sort(key=lambda c: min([s.get("queue_position") or 999 for s in c["stops"]] or [999]))
    unplanned = [{"request_id": u.get("request_id"), "reasons": u.get("reasons") or [],
                  **{k: v for k, v in info.get(u.get("request_id"), {}).items() if k in ("label", "borough")}}
                 for u in doc.get("unassigned") or []
                 if not set(u.get("reasons") or []) & {"HELD_BY_C11", "PENDING_HUMAN_REVIEW"}]
    m = doc.get("metrics") or {}
    return {"made_at": doc.get("planning_time"), "plan_id": doc.get("plan_id"), "ms": round(ms),
            "proposal_only": not (doc.get("authorization") or {}).get("dispatch_authorized", False),
            "planned": len(doc.get("assignments") or []), "pending_review": len(doc.get("pending_review") or []),
            "held": len(doc.get("held") or []), "unplanned": unplanned, "crews": crews,
            "travel_min": round(sum(c["travel_min"] for c in crews), 1), "metrics": m,
            "proposed": {a["request_id"]: {"crew_id": a["crew_id"], "sequence": a.get("sequence"),
                                           "est_start": a.get("est_start")} for a in doc.get("assignments") or []}}


# ------------------------------------------------------------------ the cached plan
def _key(records: list[dict], status: dict, now: datetime) -> str:
    locks = sorted((cid, [j.request_id for j in s.locked], s.worked_min) for cid, s in status.items())
    slot = int(now.timestamp() // REPLAN_AFTER.total_seconds())
    return hashlib.sha256(json.dumps([records, locks, slot], default=str, sort_keys=True).encode()).hexdigest()


def _make(items: list[dict], now: datetime, key: str, inputs: tuple) -> None:
    from .service import _record_calls
    t0 = time.perf_counter()
    try:
        from crew_dispatch.api import propose_plan
        records, crews, status, states = inputs
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            doc = propose_plan(records, crews, now, crew_status=status, job_states=states, seed=0)
        ms = (time.perf_counter() - t0) * 1000
        plan, call = _summary(doc, items, ms), Call(NAME, LIVE, round(ms, 2), ["crew_plan"])
    except Exception as e:  # noqa: BLE001  the safe default: no plan, staff assign by hand
        ms = (time.perf_counter() - t0) * 1000
        plan, call = None, Call(NAME, ERROR, round(ms, 2), [], note=f"{type(e).__name__}: {e}"[:300])
    _record_calls("PLAN", [call])
    assigned = _auto_assign(plan, now) if plan else 0
    with _lock:
        if plan is not None or _state["key"] != key:
            _state.update(plan=plan, made_at=now)
        # Jobs just assigned are now locked to their crews, so the next look plans again from there.
        _state.update(key=None if assigned else key, running=False, call=call)


def _auto_assign(plan: dict, now: datetime) -> int:
    """With Crew deployment on, its routes go to the crews at once, in the planned order. Staff
    can still give a job to another crew or pull it back. Switched off, nothing is assigned."""
    from .service import assign, _status_of
    n = 0
    for c in plan.get("crews", []):
        for s in sorted(c.get("stops") or [], key=lambda x: x.get("sequence") or 0):
            if _status_of(s["request_id"]) == "OPEN":
                assign(s["request_id"], c["crew_id"], NAME, at=now + timedelta(milliseconds=n))
                n += 1
    return n


def current(items: list[dict], wait: float = 0.0) -> dict:
    """The latest plan for this queue. A new one is started in the background when the queue,
    the approvals or the time have moved on. With `wait`, waits that long for it."""
    if NAME in switches.off():
        return {"outcome": OFF, "plan": None, "planning": False,
                "note": "Switched off by the tech team: staff assign crews by hand."}
    now = data.now_new_york()
    inputs = _inputs(items, now)
    key = _key(inputs[0], inputs[2], now)
    with _lock:
        start = _state["key"] != key and not _state["running"]
        if start:
            _state["running"] = True
            threading.Thread(target=_make, args=(items, now, key, inputs), daemon=True).start()
    deadline = time.monotonic() + wait
    while wait and time.monotonic() < deadline:
        with _lock:
            if _state["key"] == key and not _state["running"]:
                break
        time.sleep(0.1)
    with _lock:
        call = _state["call"]
        return {"outcome": call.outcome if call else None, "plan": _state["plan"],
                "planning": _state["running"] or _state["key"] != key,
                "note": call.note if call else ""}


def reset() -> None:
    with _lock:
        _state.update(key=None, plan=None, made_at=None, running=False, call=None)
