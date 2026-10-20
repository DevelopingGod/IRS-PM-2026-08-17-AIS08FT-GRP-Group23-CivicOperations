"""Data structures shared by the router, its rules and its callers.

The module defines:

* the vocabularies agreed in the shared data reference (emergency actions,
  special request-type labels, boroughs);
* the parsed CO1 input (:class:`CO1Request`, :class:`Location`);
* the structured output (:class:`AssignmentResult`) and its parts.

Every output object is a plain dataclass whose ``to_dict`` returns only JSON
types. The default output is the slim hand-off contract agreed with the group:
the CO1 fields passed through under their agreed names, plus agency,
required_skills, status, dispatch_eligible, next_step and explanation.

Deliberately absent from the output: estimated service time, urgency,
priority, crew, travel time, routes, external factors and coordinates. Those
belong to other components (see DECISIONS.md, D-11).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "2.0"

# Allowed values from the shared data reference, page 3.
EMERGENCY_ACTIONS = ("CONTINUE", "CONFIRM_WITH_CITIZEN", "STOP_CALL_911")
SPECIAL_LABELS = ("EMERGENCY", "OTHER", "SMALLTALK")
BOROUGHS = ("BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND")
SLOT_FIELDS = ("incident_address", "cross_street", "borough", "landmark")

DECISION_METHOD = "CATALOG_RULE_BASED"


class Status(str, Enum):
    """Overall disposition of a request after this component.

    Listed from most to least severe. When several rules fire, the most
    severe status wins (see :data:`STATUS_PRECEDENCE`).
    """

    INVALID = "INVALID"        # input breaks the CO1 contract; cannot be trusted
    BLOCKED = "BLOCKED"        # must not be dispatched (911 path / emergency label)
    HELD = "HELD"              # waits for a person or the citizen
    UNRESOLVED = "UNRESOLVED"  # no justified agency/skill assignment exists
    ASSIGNED = "ASSIGNED"      # agency and skills resolved, all gates passed


STATUS_PRECEDENCE = [Status.INVALID, Status.BLOCKED, Status.HELD, Status.UNRESOLVED, Status.ASSIGNED]


class AgencyResolution(str, Enum):
    """How (or whether) the agency/skill lookup succeeded, independent of gating."""

    RESOLVED = "RESOLVED"
    UNKNOWN_CODE = "UNKNOWN_CODE"
    CATALOG_CONFLICT = "CATALOG_CONFLICT"
    INCOMPLETE_CATALOG_ENTRY = "INCOMPLETE_CATALOG_ENTRY"
    SPECIAL_LABEL = "SPECIAL_LABEL"
    NOT_ATTEMPTED = "NOT_ATTEMPTED"  # request_type missing or malformed


class Severity(str, Enum):
    """Effect of a single rule finding on the final status."""

    INVALID = "INVALID"
    BLOCK = "BLOCK"
    HOLD = "HOLD"
    UNRESOLVED = "UNRESOLVED"
    WARNING = "WARNING"  # recorded, never changes status
    INFO = "INFO"        # recorded, never changes status


# Map each blocking severity onto the status it implies.
SEVERITY_TO_STATUS = {
    Severity.INVALID: Status.INVALID,
    Severity.BLOCK: Status.BLOCKED,
    Severity.HOLD: Status.HELD,
    Severity.UNRESOLVED: Status.UNRESOLVED,
}


class NextStep(str, Enum):
    """Workflow hint for whoever consumes the result."""

    DUPLICATE_DETECTION = "DUPLICATE_DETECTION"  # next component in the shared pipeline
    HUMAN_REVIEW = "HUMAN_REVIEW"
    CONFIRM_WITH_CITIZEN = "CONFIRM_WITH_CITIZEN"
    CITIZEN_TOLD_TO_CALL_911 = "CITIZEN_TOLD_TO_CALL_911"
    ROUTE_TO_PERSON = "ROUTE_TO_PERSON"        # OTHER label
    NO_REQUEST_CREATED = "NO_REQUEST_CREATED"  # SMALLTALK label
    FIX_INPUT = "FIX_INPUT"
    CATALOG_REVIEW = "CATALOG_REVIEW"


@dataclass(frozen=True)
class Location:
    """Location exactly as CO1 supplied it, as text. No geocoding is done here."""

    incident_address: Optional[str] = None
    cross_street: Optional[str] = None
    borough: Optional[str] = None
    landmark: Optional[str] = None

    def is_empty(self) -> bool:
        return not any((self.incident_address, self.cross_street, self.borough, self.landmark))

    def to_dict(self) -> Dict[str, Optional[str]]:
        return {
            "incident_address": self.incident_address,
            "cross_street": self.cross_street,
            "borough": self.borough,
            "landmark": self.landmark,
        }


@dataclass(frozen=True)
class CO1Request:
    """A CO1 message after type checking and light normalisation.

    Fields that failed validation are ``None``; the router still produces a
    result (status ``INVALID``) instead of raising.
    """

    request_type: Optional[str]
    confidence: Optional[float]
    ready_to_submit: Optional[bool]
    needs_human: Optional[bool]
    is_emergency: Optional[bool]
    emergency_action: Optional[str]
    location: Location = field(default_factory=Location)
    emergency_category: Optional[str] = None


@dataclass(frozen=True)
class Finding:
    """One observation produced by a business rule."""

    rule: str
    code: str
    severity: Severity
    message: str
    field: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule": self.rule,
            "code": self.code,
            "severity": self.severity.value,
            "message": self.message,
            "field": self.field,
        }


@dataclass(frozen=True)
class ReferralAdvisory:
    """Historical referral-risk knowledge for the resolved RT code (rule R14).

    It describes the past, not this request: ``historical_referral_rate`` is the
    smoothed share of training-period requests with this code (and borough,
    when supplied) that the first agency referred onward. It is *not* the
    probability that the catalog agency is wrong for this request.
    """

    risk_level: str
    historical_referral_rate: float
    basis: str
    elevated_threshold: float
    train_records: int
    matrix_relationship: str
    documented_alternative_agencies: List[str]
    context_dimension: Optional[str]
    observed_referral_destinations: Dict[str, int]
    effect: str
    note: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risk_level": self.risk_level,
            "historical_referral_rate": self.historical_referral_rate,
            "basis": self.basis,
            "elevated_threshold": self.elevated_threshold,
            "train_records": self.train_records,
            "matrix_relationship": self.matrix_relationship,
            "documented_alternative_agencies": list(self.documented_alternative_agencies),
            "context_dimension": self.context_dimension,
            "observed_referral_destinations": dict(self.observed_referral_destinations),
            "effect": self.effect,
            "note": self.note,
        }


@dataclass
class AssignmentResult:
    """Result of :meth:`AgencySkillRouter.assign`.

    ``to_dict()`` returns the **slim hand-off contract**: every field CO1 sent
    (under its agreed name, after validation) followed by the six fields this
    component adds. ``to_dict(detail=True)`` appends one ``detail`` object with
    audit information (rules, findings, referral advisory, versions).

    Invariant (tested): ``dispatch_eligible`` is true only when
    ``status == ASSIGNED``.
    """

    request: CO1Request
    status: Status
    agency: Optional[str]
    required_skills: List[str]
    dispatch_eligible: bool
    next_step: NextStep
    explanation: str
    # ---- detail-only information (kept on the object for Python callers) ----
    agency_name: Optional[str]
    citizen_label: Optional[str]
    agency_resolution: AgencyResolution
    rules_applied: List[str]
    findings: List[Finding]
    referral_advisory: Optional[ReferralAdvisory]
    catalog_version: str
    component_version: str
    decision_method: str = DECISION_METHOD
    schema_version: str = SCHEMA_VERSION

    # Convenience accessors for the pass-through fields.
    @property
    def request_type(self) -> Optional[str]:
        return self.request.request_type

    @property
    def location(self) -> Location:
        return self.request.location

    @property
    def needs_review(self) -> bool:
        """True whenever the request does not continue automatically."""
        return self.status != Status.ASSIGNED

    def to_dict(self, detail: bool = False) -> Dict[str, Any]:
        """JSON-ready dictionary in a stable key order.

        Fields CO1 sent come first, unchanged in name. A field that failed
        validation is passed on as ``null`` and the explanation says why, so
        an invalid value is never forwarded downstream.
        """
        req = self.request
        out: Dict[str, Any] = {
            # ---- received from CO1 (passed through) ----
            "request_type": req.request_type,
            "confidence": req.confidence,
            "ready_to_submit": req.ready_to_submit,
            "needs_human": req.needs_human,
            "is_emergency": req.is_emergency,
            "emergency_action": req.emergency_action,
            "emergency_category": req.emergency_category,
            "slots": req.location.to_dict(),
            # ---- added by agency and skill routing ----
            "agency": self.agency,
            "required_skills": list(self.required_skills),
            "status": self.status.value,
            "dispatch_eligible": self.dispatch_eligible,
            "next_step": self.next_step.value,
            "explanation": self.explanation,
        }
        if detail:
            out["detail"] = {
                "schema_version": self.schema_version,
                "component_version": self.component_version,
                "catalog_version": self.catalog_version,
                "decision_method": self.decision_method,
                "agency_resolution": self.agency_resolution.value,
                "agency_name": self.agency_name,
                "citizen_label": self.citizen_label,
                "needs_review": self.needs_review,
                "rules_applied": list(self.rules_applied),
                "findings": [f.to_dict() for f in self.findings],
                "referral_advisory": self.referral_advisory.to_dict() if self.referral_advisory else None,
            }
        return out

    def to_json(self, indent: Optional[int] = 2, detail: bool = False) -> str:
        return json.dumps(self.to_dict(detail=detail), indent=indent, ensure_ascii=False)
