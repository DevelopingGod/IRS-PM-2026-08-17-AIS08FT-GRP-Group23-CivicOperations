"""All thirteen components, wired in as plugins, in the order a request flows.

Rules for this file:
- An adapter only maps shared field names in and out of the owner's code.
- A safe default is the component specification's "Safe default", word for word.
- Components are named, never numbered. Module names below are the owners' own file names.
- One stated exception: the Intake bot + Emergency check adapter turns the citizen's answer to
  "Is anyone in danger right now?" into a stop or a go. That is the Emergency check's own
  designed behaviour (its spec asks exactly this question), and its owner wrote this adapter.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .. import data
from .base import Plugin, jsonable

ROOT = Path(__file__).resolve().parents[3]
COMPONENTS = ROOT / "components"
KHALID = COMPONENTS / "urgency_and_prioritisation"
URGENCY_DIR = KHALID / "C10_Urgency_Assignment"
PRIORITY_DIR = KHALID / "C11_Request_Prioritization"
MAPPINGS = json.loads((Path(__file__).resolve().parents[1] / "mappings.json").read_text(encoding="utf-8"))

for p in (ROOT, URGENCY_DIR, PRIORITY_DIR, COMPONENTS / "agency_and_skill_routing",
          COMPONENTS / "external_factors"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


def pins() -> dict:
    f = COMPONENTS / "pins.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}


def _catalog_row(req: dict) -> dict:
    return data.catalog().get(req.get("request_type") or "", {})


def _urgency_caps(level):
    """The team agreed capitals on 14 Oct. Translation table lives in mappings.json."""
    return MAPPINGS["urgency_level"].get(level, level) if level else level


# ======================================================== Sankalp: Intake bot + Emergency check
def _intake_emergency(req: dict) -> dict:
    from components import c01_nlp_bot
    history = list(req.get("history") or [])
    r = c01_nlp_bot.parse(req["text"], borough_hint=req.get("borough_hint"), history=history)
    emg = r.emergency
    action = emg.action if emg else "CONTINUE"
    why = [x.message for x in r.reasons]
    if action == "CONFIRM_WITH_CITIZEN" and req.get("citizen_confirmed_danger"):
        action = "STOP_CALL_911"
        why.append("The citizen confirmed someone is in danger right now: stop and call 911")
    elif action == "CONFIRM_WITH_CITIZEN" and req.get("citizen_confirmed_no_danger"):
        action = "CONTINUE"
        why.append("The citizen confirmed nobody is in danger right now")
    label = data.catalog().get(r.request_type_code, {}).get("citizen_label", r.request_type_code)
    return {
        "request_type": r.request_type_code, "label": label, "confidence": round(r.confidence, 3),
        "slots": jsonable(r.slots), "missing": list(r.missing_slots),
        "next_question": r.next_question, "ready_to_submit": bool(r.ready_to_submit) and action == "CONTINUE",
        "needs_human": bool(r.needs_human), "is_emergency": action == "STOP_CALL_911",
        "emergency_action": action, "emergency_category": emg.category if emg else "NONE",
        "halt": action == "STOP_CALL_911", "_why": why,
    }


def _intake_safe(req: dict) -> dict:
    # ready_to_submit = False and the citizen is offered the form. Emergency fails closed.
    return {"request_type": None, "label": None, "confidence": 0.0, "slots": {}, "missing": [],
            "next_question": "Please use the form to tell us about the problem.",
            "ready_to_submit": False, "needs_human": True, "is_emergency": False,
            "emergency_action": "CONFIRM_WITH_CITIZEN", "emergency_category": "NONE"}


# ======================================================== Mohammad: Agency and skill routing
@lru_cache(maxsize=1)
def _router():
    from civicops_agency import AgencySkillRouter
    return AgencySkillRouter.from_catalog(str(data.REF / "request_catalog.csv"))


def _agency(req: dict) -> dict:
    co1 = {k: req.get(k) for k in ("request_type", "confidence", "ready_to_submit", "needs_human",
                                   "is_emergency", "emergency_action", "emergency_category", "slots")}
    d = _router().assign(co1).to_dict()
    return {"agency": d.get("agency"), "required_skills": d.get("required_skills") or [],
            "routing_status": d.get("status"), "dispatch_eligible": bool(d.get("dispatch_eligible")),
            "_why": [d.get("explanation", "")]}


def _agency_safe(req: dict) -> dict:
    # The catalog's default agency and skill, method DEFAULT, needs_human_review = True.
    row = _catalog_row(req)
    return {"agency": row.get("agency"), "required_skills": [row["required_skill"]] if row else [],
            "routing_status": "DEFAULT", "needs_human_review": True,
            # not an extra decision: a request that needs human review is never dispatched
            "dispatch_eligible": False}


# ======================================================== Sankalp: Credibility and escalation
def _credibility(req: dict) -> dict:
    from components import c06_credibility as c
    from .. import accounts
    cid = req.get("citizen_key")          # the resident's user ID, or the 311 reporter of an imported request
    obs = [c.Observation(h["outcome"], h["closed_at"], h["request_type"]) for h in accounts.history(cid, req.get("source_request_id"))]
    r = c.assess(cid, history=obs,
                 closures_same_place_and_type=req.get("closures_same_place_and_type") or [])
    return {"credibility_score": r.credibility_score, "credibility_band": r.credibility_band,
            "observation_count": r.observation_count,
            "escalation_level": r.escalation_level, "recurrence_count": r.recurrence_count,
            "_why": [x.message for x in r.reasons]}


def _credibility_safe(req: dict) -> dict:
    # score 50, observation_count 0, no escalation.
    return {"credibility_score": 50.0, "observation_count": 0, "escalation_level": "NONE",
            "recurrence_count": 0,
            # "NEW" is what the owner's own code returns when there are no observations
            "credibility_band": "NEW"}


# ======================================================== Khalid: Urgency (with Prioritisation)
def _urgency(req: dict) -> dict:
    import c10_urgency_v11_stage6_1 as c10_urgency  # Khalid's module, from the pinned export
    r = c10_urgency.assess(urgency_message(req), root=URGENCY_DIR)
    why = [f"Rule fired: {x}" for x in r.get("matched_rules", [])] or [r.get("reason", "")]
    if r.get("review_flags"):
        why.append("Review flags: " + ", ".join(r["review_flags"]))
    return {"urgency_level": _urgency_caps(r.get("recommended_urgency")),
            "urgency_source": r.get("urgency_source"), "queue_status": r.get("c11_queue_status"),
            "_why": why}


def urgency_message(req: dict) -> dict:
    """The shared fields Khalid's Urgency reads, under his names. His Prioritisation calls
    his Urgency again inside, so the queue sends this same message."""
    return {"request_type_code": req.get("request_type"), "emergency_action": req.get("emergency_action"),
            "credibility_score": req.get("credibility_score"),
            "suspected_fake_request": MAPPINGS["suspected_fake_request"].get(req.get("fake_action") or "", False),
            # External factors names the facility category; his Urgency reads it as site text.
            "sensitive_site_type": req.get("sensitive_site_type"),
            "description": " ".join(req.get("history") or []) + " " + (req.get("text") or "")}


def _urgency_safe(req: dict) -> dict:
    # Catalog default urgency, manual_review_flag True.
    return {"urgency_level": _catalog_row(req).get("default_urgency"), "manual_review_flag": True,
            # the owner's own name for that flag is c11_queue_status = provisional_review
            "queue_status": "provisional_review", "urgency_source": "catalog default"}


# ======================================================== Nabeel: Duplicate detection
def _facts(q: dict):
    """One request in Duplicate detection's own input shape."""
    from components import c05_duplicate as d
    row = data.catalog().get(q.get("request_type") or "", {})
    slots = q.get("slots") or {}
    created = q.get("created_at")
    return d.RequestFacts(
        request_id=q.get("request_id") or "NEW",
        created_at=datetime.fromisoformat(created) if created else data.now_new_york(),
        borough=(slots.get("borough") or "").upper() or None, request_type_code=q.get("request_type"),
        complaint_type=row.get("complaint_type"), complaint_family=row.get("complaint_family"),
        channel="ONLINE", incident_address=slots.get("incident_address"),
        citizen_id=q.get("citizen_key"), free_text=q.get("text"))


def _duplicate(req: dict) -> dict:
    from components import c05_duplicate as d
    earlier = [c for c in req.get("duplicate_candidates") or [] if c.get("request_type") == req.get("request_type")]
    cands = [d.DuplicateCandidate(_facts(c), c.get("duplicate_group_id") or c["request_id"],
                                  c.get("status") != "CLOSED",
                                  datetime.fromisoformat(c["closed_at"]) if c.get("closed_at") else None,
                                  frozenset({c.get("citizen_key") or c["request_id"]}), frozenset({"ONLINE"}))
             for c in earlier]
    r = d.run(_facts(req), cands)
    # Credibility and escalation counts earlier closures at the same place and type. A
    # recurrence carries the earlier request's own list forward, plus that request.
    by_id = {c["request_id"]: c for c in earlier}
    closures = []
    if r.recurrence_of:
        closures = list((by_id.get(r.recurrence_of) or {}).get("closures_same_place_and_type") or []) + [r.recurrence_of]
    return {"duplicate_relation": r.relation.value, "is_primary": bool(r.is_primary),
            "duplicate_of": r.duplicate_of, "recurrence_of": r.recurrence_of,
            "duplicate_group_id": r.duplicate_group_id or None,
            "corroboration_count": r.corroboration_count, "match_confidence": round(r.match_confidence, 3),
            "closures_same_place_and_type": closures, "_why": r.explain()}


def _duplicate_safe(req):       # relation NEW, is_primary True. It never merges on error.
    return {"duplicate_relation": "NEW", "is_primary": True, "duplicate_count": 0,
            "closures_same_place_and_type": []}


# ======================================================== Mohammad: External factors
@lru_cache(maxsize=1)
def _evaluator():
    from civicops_external_factors import CriticalFacilityEvaluator
    return CriticalFacilityEvaluator.from_config(
        str(COMPONENTS / "external_factors" / "config" / "proximity_rules.yaml"))


def _external(req: dict) -> dict:
    slots = req.get("slots") or {}
    lat, lon = slots.get("latitude"), slots.get("longitude")   # nothing supplies these yet
    msg = {"request_id": req.get("request_id") or "NEW", "request_type": req.get("request_type"),
           "latitude": lat, "longitude": lon,
           "geolocation_status": "RESOLVED" if lat is not None else "UNRESOLVED",
           "location_precision": slots.get("location_precision") or ("ADDRESS_POINT" if lat is not None else None)}
    p = _evaluator().evaluate(msg).to_urgency_payload()
    cats = p.get("triggering_categories") or []
    return {"external_factor": p.get("external_factor"),
            "critical_facility_nearby": p.get("critical_facility_nearby"),
            "external_status": p.get("evaluation_status"),
            "external_incomplete_reason": p.get("incomplete_reason"),
            "facility_evidence": p.get("evidence"),
            "sensitive_site_type": ", ".join(c.lower().replace("_", " ") for c in cats) or None,
            "_why": [p.get("explanation") or ""]}


def _external_safe(req):        # No facilities, flags False, context_score 0 (neutral).
    return {"nearby_facilities": [], "near_sensitive_site": False, "context_score": 0,
            "external_factor": None}


# ======================================================== Nabeel: Fake request detection
def _fake(req: dict) -> dict:
    from components import c05_duplicate as d, c06_fake as f
    dup = d.DuplicateResult(component="Duplicate detection", version=d.VERSION,
                            relation=d.DuplicateRelation(req.get("duplicate_relation") or "NEW"),
                            is_primary=bool(req.get("is_primary", True)),
                            corroboration_count=int(req.get("corroboration_count") or 1))
    cred = f.CredibilitySnapshot(credibility_score=req.get("credibility_score") or 50.0,
                                 observation_count=int(req.get("observation_count") or 0),
                                 credibility_band=req.get("credibility_band") or "NEW")
    r = f.run(_facts(req), dup, cred,
              recent_reports_by_citizen_1h=int(req.get("recent_reports_by_citizen_1h") or 0))
    return {"unfounded_probability": r.unfounded_probability, "fake_action": r.recommended_action.value,
            "fake_risk_band": r.risk_band.value if r.risk_band else None,
            "reporter_burst_flag": r.reporter_burst_flag, "_why": r.explain()}


def _fake_safe(req):            # unfounded_probability None, action PROCEED.
    return {"unfounded_probability": None, "fake_action": "PROCEED"}


def _crew_safe(req):            # An empty plan (the admin assigns by hand).
    return {"crew_plan": None, "assigned_crew": None}


def _crew(req):
    # Plans the whole queue at once, so it never runs on one request: see app/backend/dispatch.py.
    raise RuntimeError("Crew deployment and routing plans the whole queue, not one request")


PLUGINS: list[Plugin] = [
    Plugin("Intake bot + Emergency check", "Sankalp", "turn",
           ["text", "history", "borough_hint"],
           ["request_type", "ready_to_submit", "emergency_action", "slots"],
           "ready_to_submit = False and the citizen is offered the form; the 911 advisory is always shown (fail closed).",
           _intake_safe, _intake_emergency, "components/intake_bot, components/emergency_check",
           "Intake bot + Emergency check"),
    Plugin("Agency and skill routing", "Mohammad", "full",
           ["request_type", "ready_to_submit", "needs_human", "emergency_action", "slots"],
           ["agency", "required_skills", "dispatch_eligible"],
           "The catalog's default agency and skill, method DEFAULT, needs_human_review = True.",
           _agency_safe, _agency, "components/agency_and_skill_routing"),
    Plugin("Duplicate detection", "Nabeel", "full", ["request_type", "slots", "duplicate_candidates"],
           ["duplicate_relation", "is_primary"],
           "relation NEW, is_primary True. It never merges on error.", _duplicate_safe, _duplicate,
           "components/duplicate_detection"),
    Plugin("External factors", "Mohammad", "full", ["slots"], ["external_factor"],
           "No facilities, flags False, context_score 0 (neutral).", _external_safe, _external,
           "components/external_factors"),
    Plugin("Credibility and escalation", "Sankalp", "full", ["citizen_key", "closures_same_place_and_type"],
           ["credibility_score", "escalation_level"],
           "score 50, observation_count 0, no escalation.",
           _credibility_safe, _credibility, "components/credibility_and_escalation"),
    Plugin("Fake request detection", "Nabeel", "full",
           ["request_type", "credibility_score", "observation_count", "duplicate_relation"],
           ["fake_action"], "unfounded_probability None, action PROCEED.", _fake_safe, _fake,
           "components/fake_request_detection"),
    Plugin("Urgency + Prioritisation", "Khalid", "full",
           ["request_type", "emergency_action", "credibility_score", "fake_action", "text"],
           ["urgency_level", "queue_status"],
           "Catalog default urgency, manual_review_flag True.",
           _urgency_safe, _urgency, "components/urgency_and_prioritisation (pinned from origin/khalid_code)",
           "Urgency + Prioritisation"),
    Plugin("Crew deployment and routing", "Mohammad", "queue",
           ["queue_status", "queue_position", "agency", "required_skills", "slots"],
           ["crew_plan"], "An empty plan: staff assign crews by hand and crews see queue order.",
           _crew_safe, _crew, "components/crew_deployment_and_routing"),
]

# Beside the pipeline, called from the staff portal.
SIDE = [
    {"name": "Predictive alerting", "owner": "Sankalp", "status": "LIVE",
     "safe_default": "No alerts, status shown as degraded on the dashboard.",
     "source": "components/predictive_alerting"},
    {"name": "Admin questions", "owner": "Nabeel", "status": "LIVE",
     "safe_default": "UNKNOWN intent with example questions.", "source": "components/admin_questions"},
]
# Resolution time and the Profanity filter were optional in the proposal and are out of scope.
# Twelve components remain; the two pairs below are counted as two each.
SWITCHABLE = [u for u in dict.fromkeys(p.unit or p.name for p in PLUGINS)] + [x["name"] for x in SIDE]


def describe() -> list[dict]:
    """What the health page shows about each component, with its pinned source."""
    p = pins()
    src = {"Agency and skill routing": (p.get("mohammad_agency") or {}).get("pinned"),
           "External factors": (p.get("mohammad_external") or {}).get("pinned"),
           "Urgency + Prioritisation": (p.get("khalid") or {}).get("pinned"),
           "Crew deployment and routing": (p.get("mohammad_crew") or {}).get("pinned")}
    from .. import switches
    off, locked = switches.off(), switches.LOCKED
    rows = [{"name": x.name, "owner": x.owner, "available": x.available, "stage": x.stage,
             "reads": x.reads, "writes": x.writes, "safe_default": x.safe_default_text,
             "source": x.source, "pinned": src.get(x.name)} for x in PLUGINS]
    rows += [{"name": x["name"], "owner": x["owner"], "available": True, "stage": "beside",
              "reads": [], "writes": [], "safe_default": x["safe_default"], "source": x["source"], "pinned": None}
             for x in SIDE]
    for r in rows:
        r["enabled"] = r["name"] not in off
        r["locked"] = locked.get(r["name"])
    return rows
