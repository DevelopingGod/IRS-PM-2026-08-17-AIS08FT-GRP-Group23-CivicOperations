"""What the portals ask for: reports, the status conversation, the ranked queue, crew jobs,
component health and measurements.

Every component call goes through the plugin runner, which applies the tech team's switches:
a switched off component runs its agreed safe default everywhere at once. The queue is built
by Khalid's Prioritisation over every open request together, because it ranks them against each
other; if it fails or is switched off, the agreed safe default is used: urgency, then oldest first.
"""
from __future__ import annotations

import contextlib
import io
import json
import math
import statistics
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import accounts, addresses, data, db, dispatch, switches
from .plugins.base import ABLATED, ERROR, LIVE, MISSING, OFF, Call, jsonable, run_pipeline, unit_names
from .plugins.registry import PLUGINS, PRIORITY_DIR, SIDE, _urgency_caps, describe, pins, urgency_message

NY = ZoneInfo("America/New_York")
LEVEL_ORDER = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
_warmed: set[str] = set()
MEASURE_FILE = db.DB.parent / "measure_result.json"   # beside the database, which is writable in a container
PRIORITISATION = "Urgency + Prioritisation"


def _iso(t: datetime) -> str:
    """Times always carry the New York offset; a time without a zone is refused."""
    if t.tzinfo is None:
        raise ValueError("time without a timezone")
    return t.astimezone(NY).isoformat(timespec="seconds")


def _record_calls(request_id: str, calls: list[Call]) -> None:
    now = _iso(data.now_new_york())
    with db.conn() as c:
        for call in calls:
            warm = 1 if call.plugin not in _warmed else 0
            _warmed.add(call.plugin)
            c.execute("insert into plugin_calls values (?,?,?,?,?,?,?)",
                      (request_id, call.plugin, call.outcome, call.ms, warm, call.note, now))


def _why(calls: list[Call]) -> list[dict]:
    """One entry per component call. The intake runs once per chat message, so its later runs
    are labelled as follow ups rather than looking like a repeat."""
    out, seen = [], {}
    for c in calls:
        seen[c.plugin] = seen.get(c.plugin, 0) + 1
        name = c.plugin if seen[c.plugin] == 1 else f"{c.plugin} (follow up message {seen[c.plugin] - 1})"
        out.append({"component": name, "outcome": c.outcome, "ms": c.ms, "reasons": c.why})
    return out


def _status_of(rid: str) -> str | None:
    with db.conn() as c:
        row = c.execute("select status from requests where id = ?", (rid,)).fetchone()
    return row["status"] if row else None


def _save(rid: str, q: dict) -> None:
    db.save_json("requests", rid, q, created_at=q["created_at"], status=_status_of(rid) or "OPEN")


# ------------------------------------------------------------------------ reporting
def locate(r: dict) -> dict:
    """Map coordinates for the address from the 311 address index, when the reporter did not pick
    one with a pin. External factors and Duplicate detection both use them."""
    slots = dict(r.get("slots") or {})
    if slots.get("latitude") is None:
        hit = addresses.lookup(slots.get("incident_address"), slots.get("borough"))
        if hit:
            slots.update({k: hit[k] for k in ("latitude", "longitude", "zip", "location_precision", "location_source")})
    return {**r, "slots": slots}


def _screen(req: dict) -> tuple[dict, list[Call], dict | None]:
    """The turn stage: the emergency check first, then the intake. Returns an early answer when
    the conversation must stop (an emergency) or ask (is anyone in danger)."""
    with contextlib.redirect_stderr(io.StringIO()):
        turn = run_pipeline(PLUGINS, req, "turn", off=switches.off())
    r = dict(turn.request)
    if turn.halted_by:
        # Nothing is filed and the resident's words are not stored. Only that it happened.
        with db.conn() as c:
            c.execute("insert into emergency_events values (?,?,?)",
                      (_iso(data.now_new_york()), r.get("emergency_category"), r.get("emergency_action")))
        _record_calls("EMERGENCY", turn.calls)
        return r, turn.calls, {"state": "EMERGENCY_STOP", "category": r.get("emergency_category")}
    if r.get("emergency_action") == "CONFIRM_WITH_CITIZEN":
        return r, turn.calls, {"state": "CONFIRM_DANGER", "question": "Is anyone in danger right now?",
                               "category": r.get("emergency_category")}
    return r, turn.calls, None


def chat(text: str, session_id: str | None, uid: str, borough_hint: str | None = None,
         confirm: str | None = None) -> dict:
    """Reporting by conversation: the Intake bot asks for whatever is missing."""
    sess = (db.load_json("sessions", session_id) if session_id else None) or {"history": [], "uid": uid}
    if sess.get("uid") != uid:
        sess = {"history": [], "uid": uid}
    session_id = session_id or uuid.uuid4().hex[:12]
    req = {"text": text, "history": sess["history"], "citizen_key": uid, "borough_hint": borough_hint,
           "citizen_confirmed_danger": confirm == "danger", "citizen_confirmed_no_danger": confirm == "no_danger"}
    r, calls, early = _screen(req)
    if early:
        if early["state"] == "EMERGENCY_STOP":
            with db.conn() as c:
                c.execute("delete from sessions where id = ?", (session_id,))
        else:
            db.save_json("sessions", session_id, sess)
            early["session_id"] = session_id
        return early
    sess["history"] = sess["history"] + [text]
    if r.get("request_type") == "SMALLTALK":
        db.save_json("sessions", session_id, sess)
        return {"state": "ASK", "session_id": session_id,
                "question": "Hello. Tell me what the problem is and where it is, and I will file it."}
    if r.get("missing"):
        db.save_json("sessions", session_id, sess)
        _record_calls(f"session:{session_id}", calls)
        return {"state": "ASK", "session_id": session_id, "question": r.get("next_question"),
                "understood": {"label": r.get("label"), "slots": r.get("slots")}}
    # Ready requests join the queue; the rest are held for a person, never dropped.
    rid = file_request(locate(r), calls, status=filing_status(r))
    with db.conn() as c:
        c.execute("delete from sessions where id = ?", (session_id,))
    return {"state": "FILED", "request_id": rid, "request": tracker(rid), "journey": journey(rid)}


def report(form: dict, uid: str) -> dict:
    """The form. The intake and emergency check still read the description, because safety does
    not depend on which screen was used. What the resident picked in the form wins over what was read."""
    text = (form.get("description") or "").strip()
    place = form.get("place") or {}
    req = {"text": text or "Problem reported with the form", "history": [], "citizen_key": uid,
           "borough_hint": place.get("borough"),
           "citizen_confirmed_danger": form.get("confirm") == "danger",
           "citizen_confirmed_no_danger": form.get("confirm") == "no_danger"}
    r, calls, early = _screen(req)
    if early:
        return early
    read_as = r.get("request_type")
    chosen = form.get("request_type")
    if chosen and chosen in data.catalog():
        r["request_type"] = chosen
        r["label"] = data.catalog()[chosen]["citizen_label"]
        if read_as != chosen:
            calls[0].why.append(f"The resident chose '{r['label']}' in the form; the description was read as {read_as}")
    slots = {**(r.get("slots") or {})}
    for k in ("incident_address", "borough", "cross_street", "latitude", "longitude", "zip",
              "location_precision", "location_source"):
        if place.get(k) not in (None, ""):
            slots[k] = place[k]
    if slots.get("borough"):
        slots["borough"] = str(slots["borough"]).upper()
    r["slots"] = slots
    r["missing"] = [k for k in ("incident_address", "borough") if not slots.get(k)]
    if r["missing"]:
        return {"state": "ASK", "question": "Please add the street address and borough.", "missing": r["missing"]}
    known = r.get("request_type") not in (None, "OTHER", "SMALLTALK")
    r["ready_to_submit"], r["needs_human"] = known, not known
    rid = file_request(locate(r), calls, status="OPEN" if known else "HELD_FOR_PERSON")
    return {"state": "FILED", "request_id": rid, "request": tracker(rid), "journey": journey(rid)}


def filing_status(r: dict) -> str:
    return "OPEN" if r.get("ready_to_submit") else "HELD_FOR_PERSON"


def earlier_requests() -> list[dict]:
    """Every stored request with its status and closing time, for Duplicate detection."""
    with db.conn() as c:
        rows = c.execute("select r.id, r.status, j.closed_at from requests r "
                         "left join crew_jobs j on j.request_id = r.id").fetchall()
    out = []
    for row in rows:
        q = db.load_json("requests", row["id"]) or {}
        out.append({**q, "request_id": row["id"], "status": row["status"], "closed_at": row["closed_at"]})
    return out


def _recent_by_citizen(earlier: list[dict], key: str | None, when: datetime) -> int:
    """How many reports this reporter filed in the hour before this one."""
    if not key:
        return 0
    n = 0
    for q in earlier:
        t = q.get("created_at")
        if t and q.get("citizen_key") == key and timedelta(0) <= when - datetime.fromisoformat(t) <= timedelta(hours=1):
            n += 1
    return n


def file_request(request: dict, turn_calls: list[Call], created: datetime | None = None,
                 ablate: set[str] | None = None, persist: bool = True, status: str = "OPEN",
                 earlier: list[dict] | None = None, live_switches: bool = True, rid: str | None = None) -> str | dict:
    """Run the full stage and store the request. Returns its id.

    `earlier` are the requests filed before this one. Stored requests are used unless a
    measurement passes its own, so a measurement never reads the live store.
    The ID is given before the components run: Duplicate detection names each incident's
    group after the request that opened it, so without an ID every request shared one group."""
    when = created or data.now_new_york()
    earlier = earlier_requests() if earlier is None else earlier
    rid = rid or "CO-" + uuid.uuid4().hex[:6].upper()
    request = {**request, "request_id": rid, "created_at": _iso(when), "duplicate_candidates": earlier,
               "recent_reports_by_citizen_1h": _recent_by_citizen(earlier, request.get("citizen_key"), when)}
    with contextlib.redirect_stderr(io.StringIO()):
        full = run_pipeline(PLUGINS, request, "full", ablate=ablate, off=switches.off() if live_switches else set())
    q = full.request
    q.pop("duplicate_candidates", None)      # used for matching, never stored with the request
    q["why"] = _why(turn_calls + full.calls)
    q["request_id"] = rid
    if not persist:
        return q
    q["filing_status"] = status
    q.setdefault("notes", [])
    db.save_json("requests", rid, q, created_at=q["created_at"], status=status)
    _record_calls(rid, turn_calls + full.calls)
    return rid


# ------------------------------------------------------------------------ what a resident sees
STAGES = {"OPEN": "In the queue", "ASSIGNED": "Crew on the way", "CLOSED": "Closed",
          "HELD_FOR_PERSON": "A person is reviewing it"}


def tracker(rid: str) -> dict | None:
    q = db.load_json("requests", rid)
    if not q:
        return None
    with db.conn() as c:
        row = c.execute("select status from requests where id = ?", (rid,)).fetchone()
        job = c.execute("select * from crew_jobs where request_id = ?", (rid,)).fetchone()
    slots = q.get("slots") or {}
    return {"request_id": rid, "label": q.get("label"), "status": row["status"], "stage": STAGES.get(row["status"]),
            "created_at": q.get("created_at"), "address": slots.get("incident_address"),
            "borough": slots.get("borough"), "agency": q.get("agency"), "urgency": q.get("urgency_level"),
            "crew_assigned": bool(job), "assigned_at": job["assigned_at"] if job else None,
            "closed_at": job["closed_at"] if job else None,
            "outcome": (q.get("closure") or {}).get("crew_outcome"),
            "latitude": slots.get("latitude"), "longitude": slots.get("longitude"),
            "request_type": q.get("request_type"), "follow_up_of": q.get("follow_up_of"),
            "family": data.catalog().get(q.get("request_type") or "", {}).get("complaint_family"),
            "notes": [n for n in q.get("notes") or [] if n.get("by") == "resident"]}


def journey(rid: str) -> list[dict]:
    """What each step decided for this report, in words a resident can read. Credibility and Fake
    request detection are left out on purpose: a resident is never shown a suspicion score."""
    q = db.load_json("requests", rid)
    if not q:
        return []
    live = {w["component"]: w["outcome"] for w in q.get("why") or []}
    dup = q.get("duplicate_relation")
    steps = [
        ("Emergency check", "Checked for danger first. Not an emergency."),
        ("Intake bot", f"Understood as: {q.get('label') or 'a question for a person'}."),
        ("Agency and skill routing", f"Sent to {q['agency']}." if q.get("agency") else "A person will choose the team."),
        ("Duplicate detection",
         f"Others reported this too. It joined {q.get('duplicate_of')}, so one crew handles it." if dup == "DUPLICATE"
         else "This came back after it was closed, so it gets more attention." if dup == "RECURRENCE"
         else "First report of this problem here."),
        ("External factors",
         f"Near a {q['sensitive_site_type']}, which is noted." if q.get("sensitive_site_type")
         else "Checked what is nearby." if q.get("external_factor") is not None else "Nearby places not checked."),
        ("Urgency + Prioritisation", f"Urgency: {(q.get('urgency_level') or 'not set').title()}."),
    ]
    out = []
    for name, text in steps:
        key = "Intake bot + Emergency check" if name in ("Emergency check", "Intake bot") else name
        outcome = live.get(key, "LIVE")
        if outcome == OFF:
            text = "Switched off by the tech team when this was filed; the agreed default was used."
        out.append({"step": name, "text": text, "outcome": outcome})
    return out


def my_requests(uid: str) -> list[dict]:
    with db.conn() as c:
        rows = c.execute("select id, data from requests order by created_at desc").fetchall()
    return [tracker(r["id"]) for r in rows if json.loads(r["data"]).get("citizen_key") == uid]


def _owned(rid: str | None, uid: str) -> dict | None:
    q = db.load_json("requests", rid) if rid else None
    return q if q and q.get("citizen_key") == uid else None


def track_chat(uid: str, text: str, current: str | None = None) -> dict:
    """The Intake bot answers status questions about the resident's own requests. If the resident
    says the problem persists after closure, it is filed again, so Duplicate detection marks it a
    recurrence and Credibility and escalation counts it."""
    from components.intake_bot import status_dialogue
    turn = status_dialogue.read(text)
    mine = my_requests(uid)
    ref = turn.reference or current
    if not ref and len(mine) == 1:
        ref = mine[0]["request_id"]
    if not ref and mine and turn.intent in ("STATUS", "PERSISTS"):
        ref = mine[0]["request_id"]          # the most recent one
    q = _owned(ref, uid) if ref else None
    intent = turn.intent
    reply, quick, action = "", [], None
    if turn.reference and not q:
        reply = f"I cannot find {turn.reference} among your requests. Check the reference, or pick one below."
    elif intent == "GREETING":
        reply = "Hello. Ask me about any of your requests, or tell me if a problem is still there."
    elif intent == "LIST" or (not q and intent in ("STATUS", "PERSISTS", "UNKNOWN")):
        reply = (f"You have {len(mine)} request{'s' if len(mine) != 1 else ''}. Pick one to see where it is."
                 if mine else "You have not reported anything yet.")
    elif intent == "PERSISTS":
        status = _status_of(ref)
        if status == "CLOSED":
            new_id = _reopen(ref, uid, text)
            reply = (f"Sorry it is still there. I have filed it again as {new_id}, linked to {ref}. "
                     "Because it came back after it was closed, it is marked as a recurrence and gets more attention.")
            action = {"type": "FOLLOW_UP_FILED", "request_id": new_id}
            ref = new_id
        else:
            add_note(ref, "resident", text)
            reply = (f"{ref} is still open ({STAGES.get(status, status).lower()}). "
                     "I have added your message to it, so the city team sees the problem is still there.")
            action = {"type": "NOTE_ADDED", "request_id": ref}
    elif intent == "FIXED":
        add_note(ref, "resident", text)
        reply = f"Thank you. I have noted on {ref} that it looks fixed. The crew's own check decides when it closes."
        action = {"type": "NOTE_ADDED", "request_id": ref}
    else:
        reply = _status_sentence(tracker(ref))
    if q or action:
        quick = ["Is it fixed yet?", "The problem is still there", "Show all my requests"]
    else:
        quick = ["Show all my requests"]
    return {"intent": intent, "reply": reply, "request": tracker(ref) if ref and _owned(ref, uid) else None,
            "journey": journey(ref) if ref and _owned(ref, uid) else [], "requests": mine if intent == "LIST" or not q else [],
            "action": action, "quick_replies": quick, "reasons": [r.message for r in turn.reasons]}


def _status_sentence(t: dict) -> str:
    where = ", ".join(x for x in (t.get("address"), (t.get("borough") or "").title()) if x)
    if t["status"] == "CLOSED":
        verdict = {"VALID_RESOLVED": "the crew fixed it", "SUBSIDED": "it had cleared before the crew arrived",
                   "INVALID_NOT_FOUND": "the crew found nothing there", "NO_ACCESS": "the crew could not get access",
                   "WRONG_AGENCY": "it was passed to another agency"}.get(t.get("outcome"), "it was closed")
        return f"{t['request_id']}, {t['label']} at {where}: closed, {verdict}. If it is still there, tell me."
    if t["status"] == "ASSIGNED":
        return f"{t['request_id']}, {t['label']} at {where}: a {t.get('agency') or 'city'} crew has been assigned."
    if t["status"] == "HELD_FOR_PERSON":
        return f"{t['request_id']}: a person is reviewing it to decide which team it needs."
    return (f"{t['request_id']}, {t['label']} at {where}: in the {t.get('agency') or 'city'} queue "
            f"with {(t.get('urgency') or 'normal').lower()} urgency. No crew is assigned yet.")


def add_note(rid: str, by: str, text: str) -> None:
    q = db.load_json("requests", rid)
    if not q:
        return
    q.setdefault("notes", []).append({"at": _iso(data.now_new_york()), "by": by, "text": text[:500]})
    _save(rid, q)


def _reopen(rid: str, uid: str, text: str) -> str:
    """File the problem again after closure, as the same type at the same place."""
    old = db.load_json("requests", rid)
    r = {k: old.get(k) for k in ("request_type", "label", "slots", "confidence", "ready_to_submit", "needs_human",
                                 "emergency_action", "emergency_category", "is_emergency")}
    r.update({"text": f"Reported again: still there after {rid} was closed. {text}"[:600], "history": [],
              "citizen_key": uid, "follow_up_of": rid})
    return file_request(r, [], status="OPEN")


# ------------------------------------------------------------------------ the ranked queue
def _prioritise(requests: list[dict], now: datetime, ablate: bool = False) -> tuple[list[dict], Call]:
    """Rank requests with Khalid's Prioritisation; fall back to urgency then oldest first."""
    t0 = time.perf_counter()
    note, outcome = "", LIVE
    ranked = None
    off = PRIORITISATION in switches.off() and not ablate
    if not ablate and not off:
        try:
            import c11_final  # Khalid's module, from the pinned export
            msgs = [{**urgency_message(q), "request_id": q["request_id"], "agency": q["agency"],
                     "created_date": q["created_at"],
                     "relation": q.get("duplicate_relation") or "NEW", "is_primary": q.get("is_primary", True),
                     "duplicate_of": q.get("duplicate_of"), "duplicate_group_id": q.get("duplicate_group_id"),
                     "corroboration_count": q.get("corroboration_count"),
                     "escalation_level": q.get("escalation_level") or "NONE",
                     "escalation_approved": bool(q.get("escalation_approved")),
                     # Prioritisation adds its sensitive site points only for an impact a person
                     # confirmed. Being near a school or hospital alone earns nothing.
                     "verified_sensitive_site_impact": bool(q.get("sensitive_impact_verified")),
                     **data.sla_category(q.get("request_type"))}
                    for q in requests]
            # Khalid's code reads its official SLA table and reviewed mapping from this folder: his
            # table and his reviewed rows, plus a default official category for the other request types.
            out = c11_final.prioritize_from_c10(msgs, _iso(now), root=data.SLA_DIR)
            by_id = {q["request_id"]: q for q in requests}
            ranked = []
            for item in out:      # already in queue order, held requests last
                base = dict(by_id[item["request_id"]])
                base.update(queue_position=item.get("overall_queue_position"),
                            urgency_position=item.get("queue_position"),
                            priority_band=item.get("priority_band"), priority_score=item.get("priority_score"),
                            priority_reasons=item.get("priority_reasons") or [],
                            urgency_level=_urgency_caps(item.get("urgency_level")) or base.get("urgency_level"),
                            queue_status=item.get("c11_queue_status"),
                            corroboration_count=item.get("corroboration_count", base.get("corroboration_count")),
                            on_hold=item.get("c11_queue_status") == "hold")
                ranked.append(base)
        except Exception as e:  # noqa: BLE001
            note, outcome = f"{type(e).__name__}: {e}"[:300], ERROR
    if ranked is None:
        outcome = ABLATED if ablate else OFF if off else outcome
        ranked = sorted(requests, key=lambda q: (
            LEVEL_ORDER.index(q.get("urgency_level")) if q.get("urgency_level") in LEVEL_ORDER else 9, q["created_at"]))
        for i, q in enumerate(ranked, 1):
            q.update(queue_position=i, priority_band=None, priority_score=None, on_hold=False,
                     priority_reasons=["Safe default: urgency level, then oldest first."])
    return ranked, Call("Prioritisation (queue)", outcome, round((time.perf_counter() - t0) * 1000, 2), [], False, note)


QUEUE_FIELDS = ["request_id", "label", "request_type", "agency", "urgency_level", "queue_position",
                "urgency_position", "priority_band", "priority_score", "priority_reasons", "on_hold",
                "queue_status", "status", "crew", "created_at", "credibility_score", "escalation_level",
                "escalation_approved", "sensitive_impact_verified", "duplicate_relation", "duplicate_of", "corroboration_count",
                "fake_action", "dispatch_eligible", "slots", "source", "external_factor", "sensitive_site_type",
                "notes", "proposed"]


def queue(record: bool = True, plan: bool = True) -> dict:
    with db.conn() as c:
        rows = c.execute("select id, status from requests where status != 'CLOSED'").fetchall()
        assigned = {r["request_id"]: r["crew_id"] for r in c.execute("select * from crew_jobs")}
    reqs = []
    for row in rows:
        q = db.load_json("requests", row["id"])
        q["status"], q["crew"] = row["status"], assigned.get(row["id"])
        reqs.append(q)
    # A request with no agency is held for a person, listed apart rather than given an invented agency.
    no_agency = [q for q in reqs if not q.get("agency")]
    reqs = [q for q in reqs if q.get("agency")]
    if not reqs:
        return {"items": [], "held_for_person": [{k: q.get(k) for k in QUEUE_FIELDS} for q in no_agency],
                "prioritisation": None}
    with contextlib.redirect_stderr(io.StringIO()):
        ranked, call = _prioritise(reqs, data.now_new_york())
    if record:
        _record_calls("QUEUE", [call])
    if plan:
        # Crew deployment's proposal for each request, kept apart from the ranking itself.
        p = dispatch.current([{k: q.get(k) for k in QUEUE_FIELDS} for q in ranked]).get("plan") or {}
        for q in ranked:
            q["proposed"] = (p.get("proposed") or {}).get(q["request_id"])
    return {"items": [{k: q.get(k) for k in QUEUE_FIELDS} for q in ranked],
            "held_for_person": [{k: q.get(k) for k in QUEUE_FIELDS} for q in no_agency],
            "prioritisation": {"outcome": call.outcome, "ms": call.ms, "note": call.note}}


def approve_escalation(rid: str, by: str) -> dict | None:
    """A person approves Credibility and escalation's recommendation. Prioritisation counts an
    escalation only once it is approved, so a recommendation alone never moves the queue."""
    q = db.load_json("requests", rid)
    if not q or (q.get("escalation_level") or "NONE") == "NONE":
        return None
    q["escalation_approved"] = True
    q["escalation_approved_at"] = _iso(data.now_new_york())
    q["escalation_approved_by"] = by
    _save(rid, q)
    return q


def confirm_impact(rid: str, by: str) -> dict | None:
    """A person confirms the problem affects the school or hospital External factors found
    nearby. Only then does Prioritisation add its sensitive site points."""
    q = db.load_json("requests", rid)
    if not q or not q.get("sensitive_site_type"):
        return None
    q["sensitive_impact_verified"] = True
    q["sensitive_impact_verified_at"] = _iso(data.now_new_york())
    q["sensitive_impact_verified_by"] = by
    _save(rid, q)
    add_note(rid, by, f"Confirmed the problem affects the nearby {q['sensitive_site_type']}")
    return q


def admin_snapshot() -> list[dict]:
    """The read only table Admin questions answers from: every request, open or closed, with its
    queue position. No reporter identity is included."""
    qd = queue(record=False)
    rows = list(qd["items"] + qd["held_for_person"])
    with db.conn() as c:
        closed = c.execute("select r.id, j.closed_at, j.crew_id from requests r "
                           "left join crew_jobs j on j.request_id = r.id where r.status = 'CLOSED'").fetchall()
    for row in closed:
        q = db.load_json("requests", row["id"]) or {}
        rows.append({**q, "request_id": row["id"], "status": "CLOSED", "crew": row["crew_id"],
                     "closed_at": row["closed_at"], "queue_position": None,
                     "resolution_status": (q.get("closure") or {}).get("verified_outcome")})
    cat = data.catalog()
    out = []
    for q in rows:
        slots = q.get("slots") or {}
        out.append({"request_id": q.get("request_id"), "label": q.get("label"),
                    "request_type": q.get("request_type"), "agency": q.get("agency"),
                    "borough": (slots.get("borough") or "").upper() or None,
                    "urgency_level": q.get("urgency_level"), "queue_position": q.get("queue_position"),
                    "status": q.get("status"), "created_at": q.get("created_at"), "crew": q.get("crew"),
                    "credibility_score": q.get("credibility_score"),
                    # Admin questions' contract names these columns; Resolution time is out of scope, so empty.
                    "eta_low_hours": None, "eta_high_hours": None,
                    "closed_at": q.get("closed_at"), "resolution_status": q.get("resolution_status"),
                    "complaint_family": cat.get(q.get("request_type") or "", {}).get("complaint_family"),
                    "priority_band": q.get("priority_band"),
                    "priority_reasons": "; ".join(q.get("priority_reasons") or [])})
    return out


def ask(question: str) -> dict:
    """Admin questions over the snapshot. Its agreed safe default answers if it fails or is off."""
    t0 = time.perf_counter()
    examples = ["How many open requests are in Brooklyn?", "Why is the first request ranked first?",
                "Which agency has the most open requests?"]
    note = ""
    if not switches.is_on("Admin questions"):
        res = {"component": "Admin questions", "outcome": OFF, "intent": "UNKNOWN",
               "answer": "Admin questions is switched off by the tech team right now.",
               "table": [], "suggestions": [], "examples": examples}
    else:
        try:
            from components import c12_admin_qa as qa
            a = qa.run(qa.AdminQuestion(question, "STAFF-PORTAL"), admin_snapshot())
            # Resolution time is out of scope, so follow ups about fix times would always come back empty.
            follow = [s for s in a.suggestions if "eta" not in s.lower().split() and "eta " not in s.lower()]
            res = {"component": "Admin questions", "outcome": LIVE, "intent": a.matched_intent,
                   "answer": a.answer_text, "table": jsonable(a.table)[:50], "suggestions": follow,
                   "confidence": a.confidence, "filters": jsonable(a.filters), "examples": []}
        except Exception as e:  # noqa: BLE001
            note = f"{type(e).__name__}: {e}"[:300]
            res = {"component": "Admin questions", "outcome": ERROR, "intent": "UNKNOWN",
                   "answer": "That question could not be answered. Here are questions it is designed to answer.",
                   "table": [], "suggestions": [], "examples": examples}
    _record_calls("ASK", [Call("Admin questions", res["outcome"], round((time.perf_counter() - t0) * 1000, 2),
                               [], False, note)])
    return res


def detail(rid: str) -> dict | None:
    q = db.load_json("requests", rid)
    if q:
        q["tracker"] = tracker(rid)
        q["status"] = _status_of(rid)
    return q


def assign(rid: str, crew_id: str, by: str, at: datetime | None = None) -> dict:
    """A person assigns a crew. This is the approval Crew deployment waits for: from now on the
    job is locked to that crew and is never planned again."""
    if crew_id not in data.crews():
        raise ValueError("No crew with that ID.")
    if _status_of(rid) not in ("OPEN", "ASSIGNED"):
        raise ValueError("This request is not open.")
    with db.conn() as c:
        c.execute("insert or replace into crew_jobs values (?,?,?,?,?)",
                  (rid, crew_id, _iso(at or data.now_new_york()), None, None))
        c.execute("update requests set status = 'ASSIGNED' where id = ?", (rid,))
    add_note(rid, by, f"Assigned to {crew_id}")
    return tracker(rid)


def crew_plan(wait: float = 0.0) -> dict:
    """Crew deployment's proposed routes for the open queue, for city staff to approve."""
    return dispatch.current(queue(record=False, plan=False)["items"], wait=wait)


def approve_route(crew_id: str, request_ids: list[str], by: str, proposed: dict | None = None) -> dict:
    """Approve a proposed route as a whole: each stop is assigned in the planned order, which
    is the order the crew then works through."""
    plan = proposed if proposed is not None else (crew_plan().get("plan") or {}).get("proposed") or {}
    wrong = [r for r in request_ids if (plan.get(r) or {}).get("crew_id") != crew_id]
    if wrong or not request_ids:
        raise ValueError(f"Not in this crew's current proposal: {', '.join(wrong)}. Reload the plan.")
    start = data.now_new_york()
    for i, rid in enumerate(sorted(request_ids, key=lambda r: plan[r].get("sequence") or 0)):
        assign(rid, crew_id, by, at=start + timedelta(milliseconds=i))
    return {"approved": len(request_ids), "crew_id": crew_id}


def approve_all_routes(by: str) -> dict:
    """Approve every route in the current proposal at once. Each crew's stops are assigned in
    their planned order, exactly as approving the routes one by one would."""
    plan = crew_plan().get("plan") or {}          # read once: every route comes from this one proposal
    crews = [c for c in plan.get("crews", []) if c.get("stops")]
    if not crews:
        raise ValueError("There is no proposed route to approve. Reload the plan.")
    stops = 0
    for c in crews:
        stops += approve_route(c["crew_id"], [s["request_id"] for s in c["stops"]], by,
                               proposed=plan.get("proposed") or {})["approved"]
    return {"approved": stops, "crews": len(crews)}


# ------------------------------------------------------------------------ crews
def crew_jobs(crew_id: str) -> dict:
    """A crew's approved jobs, in the order staff approved them: the order Crew deployment
    planned when a whole route was approved. The road it drives, and the stops it proposes
    next, come from the latest plan. With the component off or failing, the order is the
    ranked queue's, and the screen says so."""
    with db.conn() as c:
        ids = [r["request_id"] for r in c.execute(
            "select request_id from crew_jobs where crew_id = ? and outcome is null order by assigned_at", (crew_id,))]
    items = queue(record=False, plan=False)["items"]
    planned = dispatch.current(items, wait=8.0)
    route = next((c for c in (planned.get("plan") or {}).get("crews", []) if c["crew_id"] == crew_id), None)
    jobs = [tracker(i) for i in ids]
    if planned.get("outcome") == LIVE and planned.get("plan"):
        source = "Crew deployment and route optimisation, in the order city staff approved"
    else:
        order = {q["request_id"]: q.get("queue_position") or 999 for q in items}
        jobs.sort(key=lambda t: order.get(t["request_id"], 999))
        source = "The ranked queue (Crew deployment is " + ("switched off)" if planned.get("outcome") == OFF else "not available)")
    return {"crew": data.crews().get(crew_id), "jobs": jobs, "order_source": source,
            "roads": (route or {}).get("roads", []), "proposed_next": (route or {}).get("stops", []),
            "duty_end": (route or {}).get("duty_end")}


CREW_OUTCOMES = ("VALID_RESOLVED", "SUBSIDED", "INVALID_NOT_FOUND", "NO_ACCESS", "WRONG_AGENCY")


def close_job(rid: str, crew_outcome: str, crew_id: str) -> dict:
    """The crew closes its own job. Credibility's outcome verification turns what the crew saw
    into a verified outcome and updates the reporter, so the loop is closed."""
    if crew_outcome not in CREW_OUTCOMES:
        raise ValueError("outcome must be one of " + ", ".join(CREW_OUTCOMES))
    with db.conn() as c:
        job = c.execute("select crew_id, outcome from crew_jobs where request_id = ?", (rid,)).fetchone()
    if not job or job["crew_id"] != crew_id or job["outcome"]:
        raise PermissionError("This job is not open for your crew.")
    from components import c06_credibility as c
    q = db.load_json("requests", rid) or {}
    key = q.get("citizen_key")
    now = data.now_new_york()
    hist = [c.Observation(h["outcome"], h["closed_at"], h["request_type"]) for h in accounts.history(key)]
    t0 = time.perf_counter()
    res = c.verify_outcome(crew_outcome, [key] if key else [], {key: hist} if key else {},
                           now=now.replace(tzinfo=None), request_type=q.get("request_type"))
    call = Call("Credibility and escalation (outcome)", LIVE, round((time.perf_counter() - t0) * 1000, 2),
                [], False, "", [r.message for r in res.reasons])
    with db.conn() as conn:
        if key and res.verified_outcome in ("GENUINE", "UNFOUNDED"):
            conn.execute("insert into citizen_outcomes values (?,?,?,?)",
                         (key, res.verified_outcome, _iso(now), q.get("request_type")))
        conn.execute("update crew_jobs set outcome = ?, closed_at = ? where request_id = ?", (crew_outcome, _iso(now), rid))
        conn.execute("update requests set status = 'CLOSED' where id = ?", (rid,))
    q["closure"] = {"crew_outcome": crew_outcome, "verified_outcome": res.verified_outcome,
                    "credibility_change": res.credibility_deltas.get(key), "reasons": [r.message for r in res.reasons]}
    q["why"] = (q.get("why") or []) + _why([call])
    db.save_json("requests", rid, q, created_at=q["created_at"], status="CLOSED")
    _record_calls(rid, [call])
    return {**tracker(rid), "closure": q["closure"]}


# ------------------------------------------------------------------------ tech team
def health() -> dict:
    with db.conn() as c:
        rows = c.execute("select plugin, outcome, ms, warm, at from plugin_calls").fetchall()
        emergencies = c.execute("select count(*) n from emergency_events").fetchone()["n"]
        counts = {r["status"]: r["n"] for r in c.execute("select status, count(*) n from requests group by status")}
    stats = {}
    for name in sorted({r["plugin"] for r in rows}):
        mine = [r for r in rows if r["plugin"] == name]
        timed = sorted(r["ms"] for r in mine if not r["warm"] and r["outcome"] == LIVE)
        stats[name] = {
            "n": len(mine), "live": sum(r["outcome"] == LIVE for r in mine),
            "missing": sum(r["outcome"] == MISSING for r in mine), "error": sum(r["outcome"] == ERROR for r in mine),
            "off": sum(r["outcome"] == OFF for r in mine), "timed_n": len(timed),
            "median_ms": round(statistics.median(timed), 1) if timed else None,
            "p90_ms": round(timed[min(len(timed) - 1, math.ceil(0.9 * len(timed)) - 1)], 1) if timed else None,   # nearest rank
            "last_error": next((r["at"] for r in reversed(mine) if r["outcome"] == ERROR), None),
        }
    return {"components": describe(), "stats": stats, "pins": pins(), "emergency_stops": emergencies,
            "switch_history": switches.history(), "requests": counts}


def _scenario_request(s: dict, created: datetime, ablate: set[str] | None, earlier: list[dict],
                      rid: str | None = None, reporter: str | None = None) -> dict | None:
    """One evaluation scenario: the message, then its address, as a resident would answer."""
    history: list[str] = []
    calls: list[Call] = []
    for text in (s["scenario_text"], f"It is at {s['incident_address']} in {s['borough'].title()}"):
        req = {"text": text, "history": history, "citizen_key": reporter or data.typical_reporter(),
               "citizen_confirmed_no_danger": True}
        with contextlib.redirect_stderr(io.StringIO()):
            turn = run_pipeline(PLUGINS, req, "turn", ablate=ablate)
        calls += turn.calls
        r = turn.request
        if not r.get("missing") and r.get("request_type") not in (None, "SMALLTALK") and r.get("emergency_action") == "CONTINUE":
            return file_request(locate(r), calls, created=created, ablate=ablate, persist=False,
                                status=filing_status(r), earlier=earlier, live_switches=False,
                                rid=rid or s["scenario_id"])
        history = history + [text]
    return None


SECOND_REPORT_EVERY = 4


def measure() -> dict:
    """The remove one run: the 34 evaluation scenarios with everything on, then with one unit at a
    time on its safe default, counting what changes. Conditions are stated with the result."""
    from scipy.stats import kendalltau
    scen = data.evaluation_scenarios()
    start = datetime(2026, 10, 12, 8, 0, tzinfo=NY)
    now = datetime(2026, 10, 14, 12, 0, tzinfo=NY)

    def run(ablate: set[str]) -> dict:
        reqs = {}
        for i, s in enumerate(scen):
            created = start + timedelta(minutes=85 * i)
            q = _scenario_request(s, created, ablate, [{**x, "status": "OPEN"} for x in reqs.values()])
            if q:
                reqs[s["scenario_id"]] = q
            if i % SECOND_REPORT_EVERY == 0:
                # A second resident reports the same problem 40 minutes later.
                again = _scenario_request(s, created + timedelta(minutes=40), ablate,
                                          [{**x, "status": "OPEN"} for x in reqs.values()],
                                          rid=f"{s['scenario_id']}.2", reporter=f"MEASURE.SECOND.{s['scenario_id']}")
                if again:
                    reqs[again["request_id"]] = again
        for q in reqs.values():
            # The sign offs a dispatcher gives in the staff portal: approve every escalation
            # Credibility recommends, and confirm every school or hospital External factors finds.
            if (q.get("escalation_level") or "NONE") != "NONE":
                q["escalation_approved"] = True
            if q.get("sensitive_site_type"):
                q["sensitive_impact_verified"] = True
        with contextlib.redirect_stderr(io.StringIO()):
            ranked, _ = _prioritise([q for q in reqs.values() if q.get("agency")], now, ablate=PRIORITISATION in ablate)
        return {"reqs": reqs, "order": [q["request_id"] for q in ranked], "scores": [q.get("priority_score") for q in ranked]}

    total = len(scen) + sum(1 for i in range(len(scen)) if i % SECOND_REPORT_EVERY == 0)   # with the second reports
    base = run(set())
    rows = []
    for u in unit_names(PLUGINS):
        alt = run({u})
        common = [k for k in base["reqs"] if k in alt["reqs"]]

        def changed(field):
            return sum(base["reqs"][k].get(field) != alt["reqs"][k].get(field) for k in common)

        both = [k for k in base["order"] if k in alt["order"]]
        tau = None
        if len(both) > 2:
            res = kendalltau([base["order"].index(k) for k in both], [alt["order"].index(k) for k in both])
            tau = round(float(res.statistic), 3)
        rows.append({"unit": u, "filed": len(alt["reqs"]), "not_filed": total - len(alt["reqs"]),
                     "dispatch_changed": changed("dispatch_eligible"), "agency_changed": changed("agency"),
                     "urgency_changed": changed("urgency_level"), "duplicate_changed": changed("duplicate_relation"),
                     "fake_action_changed": changed("fake_action"), "queue_tau_b": tau, "queue_compared": len(both)})
    ties = len(base["scores"]) - len(set(base["scores"]))
    result = {
        "title": f"{len(scen)} evaluation scenarios and {total - len(scen)} second reports, one component at a time on its safe default",
        "second_reports": sum(1 for i in range(len(scen)) if i % SECOND_REPORT_EVERY == 0),
        "baseline_filed": len(base["reqs"]), "cases": total, "scenarios": len(scen), "rows": rows,
        "not_measurable": [p.name for p in PLUGINS if not p.available or p.stage == "queue"],
        "baseline_priority_score_ties": max(ties, 0),
        "conditions": ["Each scenario is filed in two turns: the message, then its address.",
                       "Every scenario uses the same reporter, whose real 311 record has the median confirmation rate.",
                       "Coordinates come from the 311 address index where the address is in it.",
                       f"Every {SECOND_REPORT_EVERY}th scenario is reported again 40 minutes later by a second resident, "
                       "so Duplicate detection has real duplicates to link.",
                       "A dispatcher's sign offs are applied: every escalation Credibility recommends is approved, and every "
                       "school or hospital External factors finds is confirmed. Prioritisation counts both only once a person does.",
                       "Nobody in danger is already confirmed, so the emergency path is not exercised by this run.",
                       "The tech team's live switches are ignored here: this measures the design, not today's settings.",
                       "Intake bot + Emergency check, and Urgency + Prioritisation, are switched off as pairs "
                       "because each calls the other inside its owner's code."],
        "measured_at": _iso(data.now_new_york()),
    }
    MEASURE_FILE.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def last_measure() -> dict | None:
    return json.loads(MEASURE_FILE.read_text(encoding="utf-8")) if MEASURE_FILE.exists() else None


def reset_all() -> None:
    """Empty every table, for tests and a deliberate fresh start by the tech team's command line only."""
    with db.conn() as c:
        for table in ("sessions", "requests", "plugin_calls", "emergency_events", "crew_jobs", "citizen_outcomes"):
            c.execute(f"delete from {table}")  # nosec B608: names from the fixed tuple above, command line only
    _warmed.clear()
    dispatch.reset()
    MEASURE_FILE.unlink(missing_ok=True)
