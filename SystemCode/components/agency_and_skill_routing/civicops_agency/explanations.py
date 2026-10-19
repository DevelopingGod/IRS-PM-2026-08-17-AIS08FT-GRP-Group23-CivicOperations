"""Business rule R11 - human-readable explanations.

The explanation is assembled only from the findings and lookup results that
the rules produced, so it can never describe a reason that was not actually
applied. Structure:

1. what the request type resolved to (or why it did not);
2. the outcome (status) and the reasons that set it, with rule IDs;
3. optional historical context (R14), stated as history, not as a fact about
   this request;
4. for assigned requests, the next component (duplicate detection) and a
   reminder that later components still decide priority, location and crew
   feasibility (R13).
"""

from __future__ import annotations

from typing import Dict, List, Optional

from .rules import RuleContext
from .schemas import AgencyResolution, Severity, Status

_BLOCKING = (Severity.INVALID, Severity.BLOCK, Severity.HOLD, Severity.UNRESOLVED)


def _assignment_sentence(ctx: RuleContext, agency_names: Dict[str, str], label: Optional[str]) -> str:
    code = ctx.request.request_type
    if ctx.resolution == AgencyResolution.RESOLVED:
        name = agency_names.get(ctx.agency or "")
        agency = f"{ctx.agency} ({name})" if name else str(ctx.agency)
        skills = ", ".join(ctx.skills)
        noun = "skill" if len(ctx.skills) == 1 else "skills"
        what = f"{code} ({label})" if label else str(code)
        return f"{what} was resolved through the shared catalog to {agency} with required {noun} {skills}."
    if ctx.resolution == AgencyResolution.SPECIAL_LABEL:
        return f"{code} is a special intake label, so no agency or crew skill applies."
    if ctx.resolution == AgencyResolution.NOT_ATTEMPTED:
        return "No catalog lookup was attempted because the request type is missing or malformed."
    return f"No agency was assigned to {code}."


def build_explanation(ctx: RuleContext, status: Status, agency_names: Dict[str, str],
                      citizen_label: Optional[str]) -> str:
    """Return a short paragraph explaining the result."""
    parts: List[str] = [_assignment_sentence(ctx, agency_names, citizen_label)]
    reasons = [f for f in ctx.findings if f.severity in _BLOCKING]
    if status == Status.ASSIGNED:
        parts.append("All intake, emergency and catalog checks passed, so the request continues to duplicate "
                     "detection. Later components still decide priority, location and crew feasibility.")
    else:
        verb = {
            Status.INVALID: "The input is INVALID",
            Status.BLOCKED: "The request is BLOCKED",
            Status.HELD: "The request is HELD",
            Status.UNRESOLVED: "The assignment is UNRESOLVED",
        }[status]
        reason_text = "; ".join(f"{f.message} [{f.rule}]" for f in reasons)
        parts.append(f"{verb} and is not dispatch-eligible: {reason_text}")
    if ctx.advisory is not None and ctx.advisory.risk_level == "ELEVATED":
        parts.append(f"Historical note [R14]: {ctx.advisory.note}")
    return " ".join(parts)
