"""Civic Operations · the four components Sankalp owns.

Plug and play. Nothing to configure, no paths to set, no server to start:

    from components import handle_message

    r = handle_message("the drain outside my house on nostrand ave is flooding")
    r.request_type        # 'RT021'
    r.label               # 'Catch basin clogged or flooding'
    r.is_emergency        # False
    r.ready_to_submit     # True
    r.explain()           # the reasons, in plain English

Each component can also be used on its own:

    from components import intake, emergency, credibility, alerting

    emergency.screen("I smell gas in my building").action      # 'STOP_CALL_911'
    credibility.assess("CIT-001", history=[...]).credibility_score
    alerting.forecast("BROOKLYN", date.today(), {"wx_precip_mm": 38}).alerts

The models load themselves from models/ on first use and are cached after that. If a
model file is missing every component still answers: the intake bot falls back to keyword
rules, the safeguard falls back to its lexicon and fails closed, and the forecaster
reports that it failed rather than reporting a quiet day.

Nabeel's components plug in the same way. C05 and C06 need the store's nearby candidates,
so handle_message does not call them:

    from components import duplicate, fake_risk, admin_qa

    d = duplicate.run(facts, candidates)                      # C05: NEW | DUPLICATE | RECURRENCE
    f = fake_risk.run(facts, d, credibility.assess(cid))      # C06: PROCEED | LOWER | HOLD

C12 admin Q&A reads the portal's read-only snapshot:

    a = admin_qa.run(admin_qa.AdminQuestion("How many open potholes are in Brooklyn?", "ADM-1"), rows)

The other components in the chain belong to their owners.
"""
from __future__ import annotations

import importlib
import sys
from dataclasses import dataclass, field
from datetime import date, datetime

# Each component lives in its own folder, named after the component. The old flat names
# (components.c01_nlp_bot and so on) keep working, so no caller has to change its imports.
# Order matters: Fake request detection imports Duplicate detection by its old name.
HOMES = {
    "c02_emergency": "emergency_check",
    "c01_nlp_bot": "intake_bot",
    "c06_credibility": "credibility_and_escalation",
    "c11_alerting": "predictive_alerting",
    "c05_duplicate": "duplicate_detection",
    "c06_fake": "fake_request_detection",
    "c12_admin_qa": "admin_questions",
}
for _module, _home in HOMES.items():
    _m = importlib.import_module(f"{__name__}.{_home}.{_module}")
    sys.modules[f"{__name__}.{_module}"] = _m
    globals()[_module] = _m

intake = c01_nlp_bot            # noqa: F821  (bound by the loop above)
emergency = c02_emergency       # noqa: F821
credibility = c06_credibility   # noqa: F821
alerting = c11_alerting         # noqa: F821
duplicate = c05_duplicate       # noqa: F821
fake_risk = c06_fake            # noqa: F821
admin_qa = c12_admin_qa         # noqa: F821
from .common import Reason

__all__ = ["intake", "emergency", "credibility", "alerting", "duplicate", "fake_risk", "admin_qa",
           "handle_message",
           "Incoming", "Handled", "Reason", "health_check"]

VERSION = "1.0.0"


@dataclass
class Incoming:
    """One message arriving from a citizen. Only `text` is required."""
    text: str
    citizen_id: str | None = None
    borough_hint: str | None = None
    history: list[str] = field(default_factory=list)


@dataclass
class Handled:
    """What my part of the chain produces. Everything a downstream component needs."""
    request_type: str
    label: str
    confidence: float
    is_emergency: bool
    emergency_action: str
    emergency_category: str
    slots: object
    missing: list[str]
    next_question: str | None
    ready_to_submit: bool
    needs_human: bool
    credibility_score: float | None = None
    credibility_band: str | None = None
    escalation_level: str = "NONE"
    reasons: list[Reason] = field(default_factory=list)

    def explain(self) -> list[str]:
        return [str(r) for r in self.reasons]

    def __str__(self) -> str:
        if self.is_emergency:
            return f"EMERGENCY ({self.emergency_category}): stop, call 911, nothing filed"
        state = "ready to file" if self.ready_to_submit else (self.next_question or "needs a person")
        return f"{self.request_type} {self.label} ({self.confidence:.0%}) - {state}"


def handle_message(text: str,
                   citizen_id: str | None = None,
                   borough_hint: str | None = None,
                   history: list[str] | None = None,
                   reporter_history: list | None = None,
                   recurrences: list[str] | None = None) -> Handled:
    """Run my part of the chain over one message, in the right order.

    Safety first: the emergency safeguard screens the text before anything is parsed or
    stored. If it stops, nothing else runs and nothing is filed.
    """
    parsed = intake.parse(text, borough_hint=borough_hint, history=history)
    emg = parsed.emergency
    reasons = list(parsed.reasons)

    cred_score = cred_band = None
    escalation = "NONE"
    if not (emg and emg.action == "STOP_CALL_911"):
        c = credibility.assess(citizen_id, history=reporter_history,
                               closures_same_place_and_type=recurrences)
        cred_score, cred_band = c.credibility_score, c.credibility_band
        escalation = c.escalation_level
        reasons += c.reasons

    from .c01_nlp_bot import _catalog
    label = _catalog().get(parsed.request_type_code, {}).get("citizen_label", parsed.request_type_code)

    return Handled(
        request_type=parsed.request_type_code, label=label, confidence=parsed.confidence,
        is_emergency=bool(emg and emg.action == "STOP_CALL_911"),
        emergency_action=emg.action if emg else "CONTINUE",
        emergency_category=emg.category if emg else "NONE",
        slots=parsed.slots, missing=parsed.missing_slots, next_question=parsed.next_question,
        ready_to_submit=parsed.ready_to_submit, needs_human=parsed.needs_human,
        credibility_score=cred_score, credibility_band=cred_band, escalation_level=escalation,
        reasons=reasons)


def health_check() -> dict:
    """Is everything this needs actually present? Safe to call at start-up."""
    out = {}
    try:
        out["intake model"] = "loaded" if intake._model() is not None else "missing, using keyword rules"
    except Exception as e:
        out["intake model"] = f"error: {type(e).__name__}"
    try:
        out["emergency rules"] = f"{len(emergency._lexicon())} patterns"
        out["emergency model"] = "loaded" if emergency._model() is not None else "missing, rules still run"
    except Exception as e:
        out["emergency rules"] = f"error: {type(e).__name__}"
    try:
        out["credibility base rates"] = f"{len(credibility._base_rates())} request types"
    except Exception as e:
        out["credibility base rates"] = f"error: {type(e).__name__}"
    out["demand model"] = "loaded" if alerting._bundle() is not None else "missing, forecasts report failure"
    out["duplicate (C05)"] = f"{duplicate.PRODUCTION_STRATEGY}, {len(duplicate.HYBRID_TYPE_PARAMS)} per-type settings"
    out["fake risk (C06)"] = f"{fake_risk.PRODUCTION_STRATEGY}, no REJECT action"
    out["admin Q&A (C12)"] = f"{admin_qa.PRODUCTION_STRATEGY}, read-only, {len(admin_qa.STRATEGIES)} strategies"
    return out
