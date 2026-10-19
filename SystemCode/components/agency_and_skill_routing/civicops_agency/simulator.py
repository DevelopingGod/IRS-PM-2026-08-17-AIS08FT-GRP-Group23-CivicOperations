"""CO1 simulator - structured intake messages without the real intake bot.

The simulator only *builds* CO1-shaped dictionaries. It does not read citizen
text and does not classify anything; the request type is always given to it.
That keeps tests of this component independent of the intake bot's accuracy.

Four generators are provided:

* :func:`make_co1` - one well-formed message with optional overrides;
* :func:`scenario_suite` - named positive and negative scenarios with their
  expected status, covering every catalog code and every business rule;
* :func:`random_stream` - a reproducible mixed stream for throughput tests;
* :func:`from_history_row` - a "perfect intake" message built from a historical
  311 record, using only fields CO1 can actually supply (RT code, borough).
"""

from __future__ import annotations

import copy
import random
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .catalog import Catalog
from .schemas import BOROUGHS

_DEFAULT_SLOTS = {
    "incident_address": "120 BROADWAY",
    "cross_street": "PINE STREET",
    "borough": "MANHATTAN",
    "landmark": None,
}


def make_co1(request_type: Any = "RT022", slots: Optional[Mapping[str, Any]] = None, **overrides: Any) -> Dict[str, Any]:
    """Return a valid CO1 message; keyword overrides replace top-level fields.

    ``slots`` is merged into the default location. Pass ``slots=None`` and
    ``drop=["slots"]`` to remove a field entirely.
    """
    drop = overrides.pop("drop", [])
    msg: Dict[str, Any] = {
        "request_type": request_type,
        "confidence": 0.93,
        "ready_to_submit": True,
        "needs_human": False,
        "is_emergency": False,
        "emergency_action": "CONTINUE",
        "emergency_category": None,
        "slots": copy.deepcopy(_DEFAULT_SLOTS),
    }
    if isinstance(slots, Mapping):
        msg["slots"].update(slots)
    elif slots is not None:
        msg["slots"] = slots  # deliberately malformed value, for negative tests
    msg.update(overrides)
    for key in drop:
        msg.pop(key, None)
    return msg


def scenario_suite(catalog: Catalog) -> List[Dict[str, Any]]:
    """Named scenarios with expected ``status`` and ``dispatch_eligible``.

    One ``valid_<code>`` scenario is generated per catalog code, so the suite
    grows automatically with the catalog. Expectations for catalog codes
    assume the entry is OK; the tests check catalog integrity separately.
    """
    out: List[Dict[str, Any]] = []

    def add(name: str, msg: Any, status: str, eligible: bool = False, rule: Optional[str] = None) -> None:
        out.append({"name": name, "input": msg, "expected_status": status, "expected_eligible": eligible,
                    "expected_rule": rule})

    for code in catalog.codes():
        entry = catalog.lookup(code)
        ok = entry is not None and entry.ok and entry.dispatchable is True
        add(f"valid_{code}", make_co1(code), "ASSIGNED" if ok else "UNRESOLVED", ok, "R06")

    add("unknown_code", make_co1("RT999"), "UNRESOLVED", rule="R07")
    add("missing_request_type", make_co1(drop=["request_type"]), "INVALID", rule="R01")
    add("malformed_request_type", make_co1("POTHOLE"), "INVALID", rule="R01")
    add("confidence_above_one", make_co1(confidence=1.4), "INVALID", rule="R01")
    add("confidence_wrong_type", make_co1(confidence="high"), "INVALID", rule="R01")
    add("low_confidence_default_config", make_co1(confidence=0.12), "ASSIGNED", True, "R09")
    add("not_ready_to_submit", make_co1(ready_to_submit=False), "HELD", rule="R03")
    add("needs_human", make_co1(needs_human=True), "HELD", rule="R04")
    add("stop_call_911", make_co1(is_emergency=True, emergency_action="STOP_CALL_911"), "BLOCKED", rule="R02")
    add("confirm_with_citizen", make_co1(is_emergency=True, emergency_action="CONFIRM_WITH_CITIZEN"), "HELD",
        rule="R02")
    add("contradiction_emergency_continue", make_co1(is_emergency=True, emergency_action="CONTINUE"), "HELD",
        rule="R02")
    add("contradiction_stop_not_emergency", make_co1(is_emergency=False, emergency_action="STOP_CALL_911"),
        "BLOCKED", rule="R02")
    add("unknown_emergency_action", make_co1(emergency_action="MAYBE"), "INVALID", rule="R01")
    add("label_emergency", make_co1("EMERGENCY", is_emergency=True, emergency_action="STOP_CALL_911"), "BLOCKED",
        rule="R05")
    add("label_emergency_contradictory", make_co1("EMERGENCY"), "BLOCKED", rule="R05")
    add("label_other", make_co1("OTHER"), "HELD", rule="R05")
    add("label_smalltalk", make_co1("SMALLTALK", ready_to_submit=False), "HELD", rule="R05")
    add("missing_location", make_co1(drop=["slots"]), "ASSIGNED", True, "R01")
    add("emergency_category_wrong_type", make_co1(emergency_category=5), "INVALID", rule="R01")
    add("string_boolean", make_co1(ready_to_submit="true"), "INVALID", rule="R01")
    add("not_an_object", ["RT022"], "INVALID", rule="R01")
    add("lowercase_code_normalised", make_co1(" rt022 "), "ASSIGNED", True, "R01")
    add("held_and_unknown", make_co1("RT998", needs_human=True), "HELD", rule="R04")
    return out


def random_stream(catalog: Catalog, n: int, seed: int = 7, error_rate: float = 0.15) -> List[Dict[str, Any]]:
    """Reproducible stream of mostly valid messages with a share of edge cases."""
    rng = random.Random(seed)
    codes = catalog.codes()
    edge = [
        lambda: make_co1("RT999"),
        lambda: make_co1(rng.choice(codes), needs_human=True),
        lambda: make_co1(rng.choice(codes), ready_to_submit=False),
        lambda: make_co1(rng.choice(codes), is_emergency=True, emergency_action="STOP_CALL_911"),
        lambda: make_co1("OTHER"),
        lambda: make_co1(rng.choice(codes), confidence=2.0),
    ]
    out = []
    for _ in range(n):
        if rng.random() < error_rate:
            out.append(rng.choice(edge)())
        else:
            out.append(make_co1(rng.choice(codes), confidence=round(rng.uniform(0.6, 1.0), 3),
                                slots={"borough": rng.choice(BOROUGHS)}))
    return out


def from_history_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """Build a 'perfect intake' CO1 message from a historical 311 record.

    Only ``request_type_code`` and ``borough`` are carried over; the address is
    replaced by a placeholder because location text is irrelevant to routing.
    Results are joined back to the records by position (batch order is kept).
    """
    borough = row.get("borough")
    return make_co1(
        row["request_type_code"],
        slots={"borough": borough if isinstance(borough, str) else None, "incident_address": "HISTORICAL RECORD",
               "cross_street": None},
        confidence=1.0,
    )


def iter_history_rows(rows: Iterable[Mapping[str, Any]]) -> Iterable[Dict[str, Any]]:
    for row in rows:
        yield from_history_row(row)
