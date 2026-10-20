"""Input and output data contracts for the External Factors - Critical Facility Proximity component.

The output deliberately contains NO urgency level, urgency weight, priority score or dispatch
decision. It holds geographic evidence and a binary proximity factor only.
"""

from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# --------------------------------------------------------------------------------------------
# Controlled vocabularies
# --------------------------------------------------------------------------------------------

GEOLOCATION_STATUSES = ("RESOLVED", "APPROXIMATE", "AMBIGUOUS", "UNRESOLVED", "FAILED", "NOT_ATTEMPTED")

LOCATION_PRECISIONS = (
    "ADDRESS_POINT", "GPS_DEVICE", "INTERSECTION", "BLOCKFACE", "PLACE",
    "POSTAL_CODE", "NEIGHBORHOOD", "BOROUGH", "CITY", "UNKNOWN",
)

BOROUGHS = ("BRONX", "BROOKLYN", "MANHATTAN", "QUEENS", "STATEN ISLAND")

EVALUATION_STATUSES = ("COMPLETE", "INCOMPLETE")

# Reasons an evaluation is INCOMPLETE (external_factor = None). Never converted to 0 silently.
INCOMPLETE_REASONS = {
    "MISSING_COORDINATES": "Latitude and/or longitude is missing.",
    "INVALID_COORDINATES": "Coordinates are not finite numbers in the valid latitude/longitude range.",
    "MISSING_GEOLOCATION_STATUS": "geolocation_status was not supplied, so coordinate provenance is unknown.",
    "GEOLOCATION_NOT_RESOLVED": "The Geolocation Resolution step did not resolve the location.",
    "INSUFFICIENT_PRECISION": "Location precision/uncertainty is too coarse for a proximity decision.",
    "LOCATION_UNCERTAINTY_SPANS_THRESHOLD": "The location uncertainty interval straddles the proximity threshold.",
    "OUTSIDE_COVERAGE": "Coordinates are outside the five-borough coverage area of the OSM cache.",
    "UNKNOWN_REQUEST_TYPE": "Context-aware mode needs a catalog request type to decide applicability.",
}

# Fields that must never be used as live inputs (after-the-fact leakage).
LEAKAGE_FIELDS = ("closed_date", "resolution_description", "outcome_class", "verified_outcome",
                  "resolution_hours", "log_resolution_hours", "resolution_valid",
                  "resolution_action_updated_date", "status")

# Keys that must never appear in this component's output (Urgency/Prioritisation responsibility).
FORBIDDEN_OUTPUT_KEYS = ("urgency", "urgency_level", "urgency_score", "urgency_weight", "priority",
                         "priority_score", "rank", "dispatch", "dispatch_priority", "crew_id")


def _clean_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return f


@dataclass
class RequestContext:
    """A geolocation-enriched request. Only the location fields drive spatial computation."""

    request_type: Optional[str]
    latitude: Any
    longitude: Any
    geolocation_status: Optional[str]
    location_precision: Optional[str]
    request_id: Optional[str] = None
    borough: Optional[str] = None
    location_source: Optional[str] = None
    location_uncertainty_m: Any = None
    location_type: Optional[str] = None
    ready_to_submit: Optional[bool] = None
    needs_human: Optional[bool] = None
    emergency_action: Optional[str] = None
    # Optional future intake-bot location enhancements: accepted and echoed, never required.
    address_type: Optional[str] = None
    cross_street_2: Optional[str] = None
    zip_code: Optional[str] = None
    original_location_text: Optional[str] = None
    citizen_gps: Optional[dict] = None
    extra: dict = field(default_factory=dict)

    KNOWN = ("request_type", "latitude", "longitude", "geolocation_status", "location_precision",
             "request_id", "borough", "location_source", "location_uncertainty_m", "location_type",
             "ready_to_submit", "needs_human", "emergency_action", "address_type", "cross_street_2",
             "zip_code", "original_location_text", "citizen_gps")

    @classmethod
    def from_dict(cls, d: dict) -> "RequestContext":
        if not isinstance(d, dict):
            raise TypeError(f"request context must be a JSON object, got {type(d).__name__}")
        flat = dict(d)
        # Accept intake-bot style nesting (slots.borough) and a nested location block.
        slots = flat.pop("slots", None) or {}
        if isinstance(slots, dict):
            flat.setdefault("borough", slots.get("borough"))
        loc = flat.pop("location", None)
        if isinstance(loc, dict):
            for k in ("latitude", "longitude", "geolocation_status", "location_precision",
                      "location_source", "location_uncertainty_m"):
                if k in loc:
                    flat.setdefault(k, loc[k])
        known = {k: flat.get(k) for k in cls.KNOWN}
        extra = {k: v for k, v in flat.items() if k not in cls.KNOWN}
        return cls(**known, extra=extra)

    def leakage_fields_present(self) -> list[str]:
        return [k for k in LEAKAGE_FIELDS if k in self.extra]


@dataclass
class FacilityEvidence:
    facility_id: str            # "<osm_type>/<osm_id>", e.g. "way/123456"
    osm_type: str
    osm_id: int
    facility_category: str
    name: Optional[str]
    distance_m: float           # straight-line distance to the facility GEOMETRY (0 if inside a polygon)
    geometry_type: str
    threshold_m: Optional[float] = None   # threshold applied to this facility's category (None if not qualifying)
    margin_m: Optional[float] = None      # threshold_m - distance_m (positive = inside)
    qualifies: bool = False               # category is part of the active ruleset
    borough: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["distance_m"] = round(self.distance_m, 2)
        if self.margin_m is not None:
            d["margin_m"] = round(self.margin_m, 2)
        return d


@dataclass
class LocationQuality:
    status: Optional[str]
    precision: Optional[str]
    source: Optional[str] = None
    uncertainty_m: Optional[float] = None
    uncertainty_source: Optional[str] = None        # SUPPLIED | DEFAULT_FOR_PRECISION | None
    decision_regime: Optional[str] = None           # POINT_ESTIMATE | ROBUST_INTERVAL | None
    decision_margin_m: Optional[float] = None       # best (threshold - distance) over qualifying facilities
    boundary_sensitive: Optional[bool] = None       # |margin| < uncertainty: decision could flip within uncertainty
    borough_supplied: Optional[str] = None
    borough_from_coordinates: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["decision_margin_m"] is not None:
            d["decision_margin_m"] = round(d["decision_margin_m"], 2)
        return d


@dataclass
class EvaluationResult:
    external_factor: Optional[int]
    evaluation_status: str
    incomplete_reason: Optional[str]
    critical_facility_nearby: Optional[bool]
    decision_mode: str
    thresholds_m: dict
    nearest_facility_overall: Optional[FacilityEvidence]
    nearest_qualifying_facility: Optional[FacilityEvidence]
    triggering_facility_count: int
    triggering_facilities: list
    triggering_facilities_truncated: bool
    triggered_rules: list
    explanation: str
    location_quality: LocationQuality
    warnings: list
    needs_review: bool
    request_id: Optional[str] = None
    request_type: Optional[str] = None
    ruleset_version: Optional[str] = None
    osm_data_version: Optional[str] = None
    component_version: Optional[str] = None

    def to_dict(self) -> dict:
        nq = self.nearest_qualifying_facility
        return {
            "request_id": self.request_id,
            "request_type": self.request_type,
            "external_factor": self.external_factor,
            "evaluation_status": self.evaluation_status,
            "incomplete_reason": self.incomplete_reason,
            "critical_facility_nearby": self.critical_facility_nearby,
            "decision_mode": self.decision_mode,
            "thresholds_m": self.thresholds_m,
            "nearest_qualifying_distance_m": None if nq is None else round(nq.distance_m, 2),
            "nearest_qualifying_facility": None if nq is None else nq.to_dict(),
            "nearest_facility_overall": (None if self.nearest_facility_overall is None
                                         else self.nearest_facility_overall.to_dict()),
            "triggering_facility_count": self.triggering_facility_count,
            "triggering_facilities": [f.to_dict() for f in self.triggering_facilities],
            "triggering_facilities_truncated": self.triggering_facilities_truncated,
            "triggered_rules": list(self.triggered_rules),
            "explanation": self.explanation,
            "location_quality": self.location_quality.to_dict(),
            "warnings": list(self.warnings),
            "needs_review": self.needs_review,
            "ruleset_version": self.ruleset_version,
            "osm_data_version": self.osm_data_version,
            "component_version": self.component_version,
        }

    def to_json(self, **kwargs) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, allow_nan=False, **kwargs)

    def to_urgency_payload(self) -> dict:
        """Minimal hand-off for the Urgency component: the factor plus supporting evidence.

        Contains no urgency level, weight, score or priority. Urgency decides how to use it.
        """
        top = self.triggering_facilities[0] if self.triggering_facilities else None
        return {
            "request_id": self.request_id,
            "external_factor": self.external_factor,
            "evaluation_status": self.evaluation_status,
            "incomplete_reason": self.incomplete_reason,
            "critical_facility_nearby": self.critical_facility_nearby,
            "triggered_rules": list(self.triggered_rules),
            "evidence": None if top is None else {
                "facility_category": top.facility_category,
                "facility_name": top.name,
                "distance_m": round(top.distance_m, 2),
                "threshold_m": top.threshold_m,
                "osm_id": top.facility_id,
            },
            "triggering_categories": sorted({f.facility_category for f in self.triggering_facilities}),
            "explanation": self.explanation,
            "needs_review": self.needs_review,
            "ruleset_version": self.ruleset_version,
            "osm_data_version": self.osm_data_version,
        }


def is_finite_number(x: Any) -> bool:
    f = _clean_float(x)
    return f is not None and math.isfinite(f) and not isinstance(x, bool)
