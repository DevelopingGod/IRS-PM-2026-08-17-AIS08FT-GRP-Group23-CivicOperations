"""Business rule R01 - validation of the CO1 (intake bot) message.

The contract is the one published in the shared data reference:

==========================  ========================================
Field                       Type
==========================  ========================================
request_type                str, ``RTnnn`` or EMERGENCY/OTHER/SMALLTALK
confidence                  number in [0, 1]
ready_to_submit             bool
needs_human                 bool
is_emergency                bool
emergency_action            CONTINUE / CONFIRM_WITH_CITIZEN / STOP_CALL_911
slots.incident_address      optional str
slots.cross_street          optional str
slots.borough               optional str (one of the five boroughs)
slots.landmark              optional str
==========================  ========================================

emergency_category          optional str (from the emergency check)

These are exactly the fields agreed with the intake owner. All of them are
passed through to the next component under the same names. Any other key
(e.g. ``slots.hazard_terms`` for Urgency) is ignored rather than rejected, so
CO1 can grow without breaking this component; it is not forwarded.

Validation is strict on *types* (``"true"`` is not a boolean, ``True`` is not
a confidence) because silently coercing a safety flag is worse than holding a
request for a person. Only harmless normalisation is applied: surrounding
whitespace and letter case of ``request_type``, ``emergency_action`` and
``borough``.

The validator never raises on bad input; it returns findings, and the router
turns ``INVALID`` findings into an ``INVALID`` result.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Mapping, Optional, Tuple

from .schemas import (BOROUGHS, EMERGENCY_ACTIONS, SLOT_FIELDS, SPECIAL_LABELS, CO1Request, Finding, Location,
                      Severity)

RULE = "R01"
RT_PATTERN = re.compile(r"^RT\d{3}$")
REQUIRED_BOOLEANS = ("ready_to_submit", "needs_human", "is_emergency")


def _invalid(code: str, message: str, field_name: Optional[str]) -> Finding:
    return Finding(RULE, code, Severity.INVALID, message, field_name)


def _warning(code: str, message: str, field_name: Optional[str]) -> Finding:
    return Finding(RULE, code, Severity.WARNING, message, field_name)


def _read_slots(raw: Mapping[str, Any], findings: List[Finding]) -> Location:
    """Read location slots from ``raw['slots']`` or from flat ``slots.x`` keys."""
    slots: Dict[str, Any] = {}
    nested = raw.get("slots")
    if nested is not None and not isinstance(nested, Mapping):
        findings.append(_invalid("INVALID_SLOTS", "slots must be an object (dictionary) or null.", "slots"))
        nested = None
    if isinstance(nested, Mapping):
        slots.update({k: nested.get(k) for k in SLOT_FIELDS if k in nested})
    # The published contract writes fields as 'slots.incident_address'; accept
    # that flat spelling too. Nested values take precedence if both exist.
    for name in SLOT_FIELDS:
        flat = f"slots.{name}"
        if flat in raw and name not in slots:
            slots[name] = raw[flat]

    clean: Dict[str, Optional[str]] = {}
    for name in SLOT_FIELDS:
        value = slots.get(name)
        if value is None:
            clean[name] = None
        elif not isinstance(value, str):
            findings.append(_invalid("INVALID_SLOT_TYPE", f"slots.{name} must be a string or null, got "
                                     f"{type(value).__name__}.", f"slots.{name}"))
            clean[name] = None
        else:
            clean[name] = value.strip() or None

    borough = clean["borough"]
    if borough is not None:
        upper = " ".join(borough.upper().split())
        if upper in BOROUGHS:
            clean["borough"] = upper
        else:
            # Location is context for later components, not a routing input,
            # so an unexpected borough is recorded but does not block.
            findings.append(_warning("UNKNOWN_BOROUGH", f"Borough {borough!r} is not one of {', '.join(BOROUGHS)}; "
                                     "kept as supplied.", "slots.borough"))
    loc = Location(**clean)
    if loc.is_empty():
        findings.append(_warning("LOCATION_MISSING", "No location slots supplied; later components need a "
                                 "location before a crew can be sent.", "slots"))
    return loc


def validate_co1(raw: Any, normalize_request_type: bool = True) -> Tuple[CO1Request, List[Finding]]:
    """Validate and normalise one CO1 message.

    Args:
        raw: the decoded JSON object (a mapping).
        normalize_request_type: strip whitespace and upper-case the code
            (``" rt022"`` -> ``"RT022"``), recording a warning when it changes.

    Returns:
        ``(request, findings)``. ``request`` always exists; invalid fields are
        ``None``. ``findings`` holds every R01 observation.
    """
    findings: List[Finding] = []
    if not isinstance(raw, Mapping):
        findings.append(_invalid("NOT_AN_OBJECT", f"CO1 output must be a JSON object, got {type(raw).__name__}.",
                                 None))
        return CO1Request(None, None, None, None, None, None, Location()), findings

    # ---- request_type ------------------------------------------------------
    raw_rt = raw.get("request_type")
    request_type: Optional[str] = None
    if "request_type" not in raw or raw_rt is None:
        findings.append(_invalid("MISSING_REQUEST_TYPE", "request_type is required.", "request_type"))
    elif not isinstance(raw_rt, str):
        findings.append(_invalid("INVALID_REQUEST_TYPE_TYPE",
                                 f"request_type must be a string, got {type(raw_rt).__name__}.", "request_type"))
    else:
        candidate = raw_rt.strip().upper() if normalize_request_type else raw_rt
        if candidate != raw_rt:
            findings.append(_warning("REQUEST_TYPE_NORMALISED",
                                     f"request_type {raw_rt!r} normalised to {candidate!r}.", "request_type"))
        if not candidate:
            findings.append(_invalid("MISSING_REQUEST_TYPE", "request_type is empty.", "request_type"))
        elif RT_PATTERN.match(candidate) or candidate in SPECIAL_LABELS:
            request_type = candidate
        else:
            findings.append(_invalid("MALFORMED_REQUEST_TYPE",
                                     f"request_type {raw_rt!r} is neither an RT code (RT + 3 digits) nor one of "
                                     f"{', '.join(SPECIAL_LABELS)}.", "request_type"))

    # ---- confidence ----------------------------------------------------------
    raw_conf = raw.get("confidence")
    confidence: Optional[float] = None
    if "confidence" not in raw or raw_conf is None:
        findings.append(_invalid("MISSING_CONFIDENCE", "confidence is required.", "confidence"))
    elif isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
        findings.append(_invalid("INVALID_CONFIDENCE_TYPE",
                                 f"confidence must be a number, got {type(raw_conf).__name__}.", "confidence"))
    elif not math.isfinite(raw_conf) or not 0.0 <= raw_conf <= 1.0:
        findings.append(_invalid("CONFIDENCE_OUT_OF_RANGE", f"confidence {raw_conf!r} is outside [0, 1].",
                                 "confidence"))
    else:
        confidence = float(raw_conf)

    # ---- booleans ------------------------------------------------------------
    flags: Dict[str, Optional[bool]] = {}
    for name in REQUIRED_BOOLEANS:
        value = raw.get(name)
        if name not in raw or value is None:
            findings.append(_invalid(f"MISSING_{name.upper()}", f"{name} is required.", name))
            flags[name] = None
        elif not isinstance(value, bool):
            findings.append(_invalid(f"INVALID_{name.upper()}_TYPE",
                                     f"{name} must be true or false, got {type(value).__name__} {value!r}.", name))
            flags[name] = None
        else:
            flags[name] = value

    # ---- emergency_action ------------------------------------------------------
    raw_action = raw.get("emergency_action")
    action: Optional[str] = None
    if "emergency_action" not in raw or raw_action is None:
        findings.append(_invalid("MISSING_EMERGENCY_ACTION", "emergency_action is required.", "emergency_action"))
    elif not isinstance(raw_action, str):
        findings.append(_invalid("INVALID_EMERGENCY_ACTION_TYPE",
                                 f"emergency_action must be a string, got {type(raw_action).__name__}.",
                                 "emergency_action"))
    else:
        candidate = raw_action.strip().upper()
        if candidate in EMERGENCY_ACTIONS:
            action = candidate
        else:
            findings.append(_invalid("UNKNOWN_EMERGENCY_ACTION",
                                     f"emergency_action {raw_action!r} is not one of {', '.join(EMERGENCY_ACTIONS)}.",
                                     "emergency_action"))

    # ---- emergency_category (optional; set by the emergency check) -----------
    category = raw.get("emergency_category")
    if category is not None and not isinstance(category, str):
        findings.append(_invalid("INVALID_EMERGENCY_CATEGORY_TYPE",
                                 f"emergency_category must be a string or null, got {type(category).__name__}.",
                                 "emergency_category"))
        category = None
    elif isinstance(category, str):
        category = category.strip() or None

    location = _read_slots(raw, findings)
    request = CO1Request(
        request_type=request_type,
        confidence=confidence,
        ready_to_submit=flags["ready_to_submit"],
        needs_human=flags["needs_human"],
        is_emergency=flags["is_emergency"],
        emergency_action=action,
        location=location,
        emergency_category=category,
    )
    return request, findings
