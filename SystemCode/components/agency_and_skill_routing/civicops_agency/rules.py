"""Explicit business rules R01-R14 for agency and skill assignment.

This is the knowledge-based core of the component (course technique:
*decision automation with business rules*). Each rule:

* has a stable ID and a one-line statement in :data:`RULES` (the rule
  register shown by ``civicops-agency rules`` and in the report);
* is a small function that reads the shared :class:`RuleContext` and appends
  :class:`~civicops_agency.schemas.Finding` objects;
* never decides the final status by itself. Statuses are combined in
  :func:`r13_eligibility` by a fixed precedence
  (INVALID > BLOCKED > HELD > UNRESOLVED > ASSIGNED), so adding a rule can
  only make a request *less* eligible, never more. That monotonicity is what
  makes it safe to say "a request cannot be dispatch-eligible if any blocking
  rule applies".

R01 lives in :mod:`civicops_agency.validators`; R11 (explanation) lives in
:mod:`civicops_agency.explanations`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .catalog import Catalog, CatalogEntry
from .config import RouterConfig
from .knowledge import ReferralKnowledge
from .schemas import (STATUS_PRECEDENCE, SEVERITY_TO_STATUS, AgencyResolution, CO1Request, Finding, NextStep,
                      ReferralAdvisory, Severity, Status)


@dataclass(frozen=True)
class BusinessRule:
    id: str
    name: str
    statement: str


RULES: Dict[str, BusinessRule] = {r.id: r for r in [
    BusinessRule("R01", "Validate CO1 input",
                 "Required fields, types, confidence range and request-type format must match the CO1 contract; "
                 "otherwise the result is INVALID."),
    BusinessRule("R02", "Emergency restriction",
                 "Only emergency_action CONTINUE may proceed. STOP_CALL_911 blocks; CONFIRM_WITH_CITIZEN holds; "
                 "contradictory emergency fields hold for a person."),
    BusinessRule("R03", "Submission readiness", "ready_to_submit = false holds the request."),
    BusinessRule("R04", "Human review", "needs_human = true holds the request for a person."),
    BusinessRule("R05", "Special request types",
                 "EMERGENCY blocks (citizen told to call 911); OTHER and SMALLTALK are held and follow the intake "
                 "workflow instead of agency/skill dispatch."),
    BusinessRule("R06", "Exact catalog lookup", "A well-formed RT code is resolved only by exact match in the "
                 "shared catalog."),
    BusinessRule("R07", "Unknown codes", "A code absent from the catalog is UNRESOLVED; no agency is invented."),
    BusinessRule("R08", "Catalog integrity", "Duplicate-conflicting or incomplete catalog entries are never "
                 "resolved by picking a row; the code is UNRESOLVED and flagged for catalog review."),
    BusinessRule("R09", "CO1 confidence", "Intake confidence is passed through unchanged as confidence and is not "
                 "the probability that the agency is correct. An optional, configurable min_confidence holds low-"
                 "confidence requests."),
    BusinessRule("R10", "Required skills", "Skills come only from the catalog entry; a missing skill makes the "
                 "assignment UNRESOLVED."),
    BusinessRule("R11", "Explanation", "Every result carries a human-readable explanation naming each blocking "
                 "rule; the full list of rules applied is in the optional detail block."),
    BusinessRule("R12", "Safe unresolved state", "If agency or skills cannot be justified, agency and skills are "
                 "emptied and the result is held for review rather than guessed."),
    BusinessRule("R13", "Downstream eligibility", "dispatch_eligible requires a resolved agency and skills, "
                 "ready_to_submit, no human-review need, no emergency block and a dispatchable catalog entry. It "
                 "does not guarantee a crew can be sent."),
    BusinessRule("R14", "Historical referral advisory", "When train-period history shows the code's first agency "
                 "often referred requests onward, attach an informational advisory. It never changes agency or "
                 "skills; it holds the request only if review_on_elevated_referral_risk is configured."),
]}


@dataclass
class RuleContext:
    """Mutable working memory shared by the rules for one request."""

    request: CO1Request
    config: RouterConfig
    findings: List[Finding] = field(default_factory=list)
    evaluated: List[str] = field(default_factory=list)
    entry: Optional[CatalogEntry] = None
    resolution: AgencyResolution = AgencyResolution.NOT_ATTEMPTED
    agency: Optional[str] = None
    skills: List[str] = field(default_factory=list)
    advisory: Optional[ReferralAdvisory] = None

    def mark(self, rule_id: str) -> None:
        if rule_id not in self.evaluated:
            self.evaluated.append(rule_id)

    def add(self, rule_id: str, code: str, severity: Severity, message: str, field_name: Optional[str] = None) -> None:
        self.mark(rule_id)
        self.findings.append(Finding(rule_id, code, severity, message, field_name))

    def has(self, code: str) -> bool:
        return any(f.code == code for f in self.findings)


# ---------------------------------------------------------------------------
# R02 - emergency restriction
# ---------------------------------------------------------------------------
def r02_emergency(ctx: RuleContext) -> None:
    req = ctx.request
    ctx.mark("R02")
    action, flagged = req.emergency_action, req.is_emergency
    if action is None:
        return  # already INVALID under R01; nothing can proceed anyway
    if action == "STOP_CALL_911":
        ctx.add("R02", "EMERGENCY_STOP_CALL_911", Severity.BLOCK,
                "The emergency check returned STOP_CALL_911: the citizen is directed to 911 and nothing is "
                "dispatched.", "emergency_action")
        if flagged is False:
            ctx.add("R02", "EMERGENCY_FIELDS_INCONSISTENT", Severity.WARNING,
                    "emergency_action is STOP_CALL_911 but is_emergency is false; the stricter action was applied.",
                    "is_emergency")
    elif action == "CONFIRM_WITH_CITIZEN":
        ctx.add("R02", "EMERGENCY_CONFIRM_WITH_CITIZEN", Severity.HOLD,
                "The emergency check asked to confirm with the citizen before anything moves on.",
                "emergency_action")
    elif flagged is True:
        # CONTINUE together with is_emergency = true cannot both be right.
        # Fail safe: do not dispatch, and send to a person.
        ctx.add("R02", "EMERGENCY_CONTRADICTION", Severity.HOLD,
                "is_emergency is true but emergency_action is CONTINUE; the request is held for a person to "
                "resolve the contradiction.", "is_emergency")


# ---------------------------------------------------------------------------
# R03 / R04 - readiness and human review
# ---------------------------------------------------------------------------
def r03_readiness(ctx: RuleContext) -> None:
    ctx.mark("R03")
    if ctx.request.ready_to_submit is False:
        ctx.add("R03", "NOT_READY_TO_SUBMIT", Severity.HOLD,
                "CO1 marked the request as not ready to submit (details are still being collected).",
                "ready_to_submit")


def r04_human_review(ctx: RuleContext) -> None:
    ctx.mark("R04")
    if ctx.request.needs_human is True:
        ctx.add("R04", "NEEDS_HUMAN", Severity.HOLD, "CO1 asked for a person to review this request.",
                "needs_human")


# ---------------------------------------------------------------------------
# R05 - special request types
# ---------------------------------------------------------------------------
def r05_special_labels(ctx: RuleContext) -> bool:
    """Handle EMERGENCY/OTHER/SMALLTALK. Returns True if the label was special."""
    label = ctx.request.request_type
    ctx.mark("R05")
    if label == "EMERGENCY":
        ctx.add("R05", "SPECIAL_LABEL_EMERGENCY", Severity.BLOCK,
                "Request type EMERGENCY: someone may be in danger; the citizen is told to call 911 and nothing is "
                "filed.", "request_type")
        if ctx.request.emergency_action == "CONTINUE" or ctx.request.is_emergency is False:
            ctx.add("R05", "EMERGENCY_LABEL_CONTRADICTION", Severity.WARNING,
                    "Request type is EMERGENCY but the emergency fields do not indicate an emergency; the "
                    "safer interpretation (block) was applied.", "request_type")
    elif label == "OTHER":
        ctx.add("R05", "SPECIAL_LABEL_OTHER", Severity.HOLD,
                "Request type OTHER: a real question no crew can fix; it goes to a person.", "request_type")
    elif label == "SMALLTALK":
        ctx.add("R05", "SPECIAL_LABEL_SMALLTALK", Severity.HOLD,
                "Request type SMALLTALK: no service request is created.", "request_type")
    else:
        return False
    ctx.resolution = AgencyResolution.SPECIAL_LABEL
    return True


# ---------------------------------------------------------------------------
# R06 / R07 / R08 / R10 - catalog resolution
# ---------------------------------------------------------------------------
def r06_catalog_lookup(ctx: RuleContext, catalog: Catalog) -> None:
    code = ctx.request.request_type
    if code is None:
        return  # malformed or missing; R01 already made this INVALID
    ctx.mark("R06")
    entry = catalog.lookup(code)
    if entry is None:
        ctx.resolution = AgencyResolution.UNKNOWN_CODE
        ctx.add("R07", "UNKNOWN_REQUEST_TYPE", Severity.UNRESOLVED,
                f"{code} is not in the shared request catalog (version {catalog.version}); no agency is assigned.",
                "request_type")
        return
    ctx.entry = entry
    if entry.state == "CONFLICT":
        ctx.resolution = AgencyResolution.CATALOG_CONFLICT
        ctx.add("R08", "CATALOG_CONFLICT", Severity.UNRESOLVED,
                f"The catalog has conflicting rows for {code} (agencies {list(entry.candidate_agencies)}); the "
                "router will not choose between them.", "request_type")
        return
    if entry.state == "INCOMPLETE":
        ctx.resolution = AgencyResolution.INCOMPLETE_CATALOG_ENTRY
        if entry.agency is None:
            ctx.add("R08", "CATALOG_MISSING_AGENCY", Severity.UNRESOLVED,
                    f"The catalog entry for {code} has no agency.", "request_type")
        if not entry.required_skills:
            ctx.add("R10", "CATALOG_MISSING_SKILLS", Severity.UNRESOLVED,
                    f"The catalog entry for {code} has no required skill.", "request_type")
        return
    ctx.resolution = AgencyResolution.RESOLVED
    ctx.agency = entry.agency


def r10_skills(ctx: RuleContext) -> None:
    if ctx.resolution != AgencyResolution.RESOLVED or ctx.entry is None:
        return
    ctx.mark("R10")
    ctx.skills = list(ctx.entry.required_skills)


# ---------------------------------------------------------------------------
# R09 - confidence
# ---------------------------------------------------------------------------
def r09_confidence(ctx: RuleContext) -> None:
    ctx.mark("R09")
    conf, threshold = ctx.request.confidence, ctx.config.min_confidence
    if conf is not None and threshold is not None and conf < threshold:
        ctx.add("R09", "LOW_INTAKE_CONFIDENCE", Severity.HOLD,
                f"Intake confidence {conf:.2f} is below the configured review threshold {threshold:.2f}.",
                "confidence")


# ---------------------------------------------------------------------------
# R14 - historical referral advisory
# ---------------------------------------------------------------------------
def r14_referral_advisory(ctx: RuleContext, knowledge: Optional[ReferralKnowledge]) -> None:
    if knowledge is None or ctx.resolution != AgencyResolution.RESOLVED:
        return
    if any(f.severity in (Severity.INVALID, Severity.BLOCK) for f in ctx.findings):
        return  # nothing will be dispatched; historical context would only add noise
    code = ctx.request.request_type or ""
    advisory = knowledge.advisory(code, ctx.request.location.borough, ctx.config.review_on_elevated_referral_risk)
    if advisory is None:
        return
    ctx.mark("R14")
    ctx.advisory = advisory
    if advisory.risk_level == "ELEVATED":
        severity = Severity.HOLD if advisory.effect == "REVIEW_REQUIRED" else Severity.INFO
        ctx.add("R14", "ELEVATED_REFERRAL_RISK", severity, advisory.note, "request_type")


# ---------------------------------------------------------------------------
# R12 / R13 - safe state and eligibility
# ---------------------------------------------------------------------------
def r12_safe_unresolved(ctx: RuleContext) -> None:
    if ctx.resolution in (AgencyResolution.RESOLVED,):
        return
    ctx.mark("R12")
    # Never leak a partial assignment (e.g. agency without skills).
    ctx.agency = None
    ctx.skills = []


def r13_eligibility(ctx: RuleContext) -> Status:
    """Combine findings into one status using the fixed precedence."""
    ctx.mark("R13")
    if ctx.entry is not None and ctx.resolution == AgencyResolution.RESOLVED and ctx.entry.dispatchable is not True:
        reason = ("is marked not dispatchable" if ctx.entry.dispatchable is False
                  else "has an unreadable dispatchable flag")
        ctx.add("R13", "CATALOG_NOT_DISPATCHABLE", Severity.HOLD,
                f"The catalog entry for {ctx.entry.code} {reason}; it goes to a person.", "request_type")
    implied = {SEVERITY_TO_STATUS[f.severity] for f in ctx.findings if f.severity in SEVERITY_TO_STATUS}
    if ctx.resolution != AgencyResolution.RESOLVED:
        # Defensive: an unresolved assignment can never be ASSIGNED.
        implied.add(Status.UNRESOLVED)
    for status in STATUS_PRECEDENCE:
        if status in implied:
            return status
    return Status.ASSIGNED


def next_step_for(status: Status, ctx: RuleContext) -> NextStep:
    """Workflow hint derived from the status and the most relevant finding."""
    if status == Status.ASSIGNED:
        return NextStep.DUPLICATE_DETECTION
    if status == Status.INVALID:
        return NextStep.FIX_INPUT
    if status == Status.BLOCKED:
        return NextStep.CITIZEN_TOLD_TO_CALL_911
    if status == Status.HELD:
        if ctx.has("EMERGENCY_CONFIRM_WITH_CITIZEN"):
            return NextStep.CONFIRM_WITH_CITIZEN
        if ctx.has("SPECIAL_LABEL_SMALLTALK"):
            return NextStep.NO_REQUEST_CREATED
        if ctx.has("SPECIAL_LABEL_OTHER"):
            return NextStep.ROUTE_TO_PERSON
        return NextStep.HUMAN_REVIEW
    if ctx.resolution in (AgencyResolution.CATALOG_CONFLICT, AgencyResolution.INCOMPLETE_CATALOG_ENTRY):
        return NextStep.CATALOG_REVIEW
    return NextStep.HUMAN_REVIEW
