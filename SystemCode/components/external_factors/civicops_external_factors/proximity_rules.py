"""Configurable, transparent proximity ruleset and the binary decision logic.

The rules work on spatial evidence that the spatial engine has already computed: distances to
qualifying facilities. They never look at request-type severity. Request type is used only in
the opt-in CONTEXT_AWARE mode, to decide whether the factor is spatially applicable (public
realm versus building interior), and only if the team agrees to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import yaml

DECISION_MODES = ("PURE_GEOGRAPHIC", "CONTEXT_AWARE")

# Numerical tolerance for the inclusive boundary test distance <= threshold (1 micrometre).
BOUNDARY_EPS_M = 1e-6

# Rule catalogue: identifier -> description. Category proximity rules are defined in the YAML.
STATIC_RULES = {
    "CRITICAL_PROXIMITY_NONE": "No qualifying critical facility within its configured threshold (complete evaluation).",
    "LOCATION_POINT_ESTIMATE": "Location precise enough: decision taken on the supplied point.",
    "LOCATION_ROBUST_INTERVAL": "Approximate location: decision holds for every point within the uncertainty radius.",
    "CONTEXT_NOT_APPLICABLE": "Context-aware mode: request type is a building-interior condition; proximity factor not applied.",
    "INPUT_MISSING_COORDINATES": "Latitude and/or longitude missing.",
    "INPUT_INVALID_COORDINATES": "Coordinates out of range or not numeric.",
    "INPUT_MISSING_GEOLOCATION_STATUS": "geolocation_status missing.",
    "INPUT_GEOLOCATION_NOT_RESOLVED": "Geolocation status not accepted for evaluation.",
    "INPUT_INSUFFICIENT_PRECISION": "Location precision/uncertainty too coarse.",
    "INPUT_UNCERTAINTY_SPANS_THRESHOLD": "Uncertainty interval straddles the threshold; decision indeterminate.",
    "INPUT_OUTSIDE_COVERAGE": "Location outside the five-borough coverage of the OSM cache.",
    "INPUT_UNKNOWN_REQUEST_TYPE": "Context-aware mode cannot determine applicability for an unknown request type.",
}


class RulesetError(ValueError):
    """Raised when the proximity ruleset configuration is invalid."""


@dataclass(frozen=True)
class CategoryRule:
    category: str
    threshold_m: float
    rule_id: str
    description: str = ""


@dataclass
class LocationPolicy:
    accepted_geolocation_status: tuple
    precision_aliases: dict
    default_uncertainty_m: dict
    unsupported_precision: tuple
    point_decision_max_uncertainty_m: float
    max_evaluable_uncertainty_m: float
    request_coverage_tolerance_m: float


@dataclass
class ContextAwarePolicy:
    not_applicable_request_types: tuple   # building-interior conditions; every other catalog type is applicable
    rationale: str = ""


@dataclass
class ProximityRuleset:
    ruleset_version: str
    decision_mode: str
    metric_crs: str
    category_rules: dict                     # category -> CategoryRule (qualifying categories)
    evidence_categories: tuple               # categories reported as "nearest overall" evidence
    location: LocationPolicy
    context_aware: Optional[ContextAwarePolicy]
    max_facilities_listed: int = 10
    include_duplicates: bool = False
    review_boundary_sensitive: bool = False
    request_catalog_path: Optional[str] = None
    osm_data_dir: Optional[str] = None
    provenance: dict = field(default_factory=dict)
    source_path: Optional[str] = None

    # ----------------------------------------------------------------------------------------
    @classmethod
    def from_yaml(cls, path: str | Path, overrides: Optional[dict] = None) -> "ProximityRuleset":
        path = Path(path)
        if not path.exists():
            raise RulesetError(f"Ruleset configuration not found: {path}")
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
        if overrides:
            raw = _deep_merge(raw, overrides)
        rs = cls.from_dict(raw)
        rs.source_path = str(path)
        return rs

    @classmethod
    def from_dict(cls, raw: dict) -> "ProximityRuleset":
        try:
            mode = str(raw.get("decision_mode", "PURE_GEOGRAPHIC")).upper()
            cats = raw["facility_categories"]
            loc = raw["location"]
        except KeyError as exc:
            raise RulesetError(f"Missing required ruleset section: {exc}") from exc
        if mode not in DECISION_MODES:
            raise RulesetError(f"decision_mode must be one of {DECISION_MODES}, got {mode!r}")
        if not isinstance(cats, dict) or not cats:
            raise RulesetError("facility_categories must define at least one qualifying category")
        rules = {}
        for cat, spec in cats.items():
            spec = spec or {}
            if spec.get("enabled", True) is False:
                continue
            thr = spec.get("threshold_m", raw.get("default_threshold_m"))
            if thr is None or isinstance(thr, bool):
                raise RulesetError(f"Category {cat}: threshold_m missing")
            try:
                thr = float(thr)
            except (TypeError, ValueError) as exc:
                raise RulesetError(f"Category {cat}: threshold_m must be a number, got {thr!r}") from exc
            if not (thr > 0) or thr != thr or thr == float("inf"):
                raise RulesetError(f"Category {cat}: threshold_m must be a positive finite number, got {thr}")
            rules[cat] = CategoryRule(cat, thr, spec.get("rule_id", f"CRITICAL_PROXIMITY_{cat}"),
                                      " ".join(str(spec.get("description", "")).split()))
        if not rules:
            raise RulesetError("All facility categories are disabled")
        policy = LocationPolicy(
            accepted_geolocation_status=tuple(s.upper() for s in loc["accepted_geolocation_status"]),
            precision_aliases={str(k).upper(): str(v).upper() for k, v in (loc.get("precision_aliases") or {}).items()},
            default_uncertainty_m={str(k).upper(): float(v) for k, v in loc["default_uncertainty_m"].items()},
            unsupported_precision=tuple(s.upper() for s in loc.get("unsupported_precision", [])),
            point_decision_max_uncertainty_m=float(loc["point_decision_max_uncertainty_m"]),
            max_evaluable_uncertainty_m=float(loc["max_evaluable_uncertainty_m"]),
            request_coverage_tolerance_m=float(loc.get("request_coverage_tolerance_m", 0)),
        )
        if policy.point_decision_max_uncertainty_m < 0 or policy.max_evaluable_uncertainty_m < policy.point_decision_max_uncertainty_m:
            raise RulesetError("Require 0 <= point_decision_max_uncertainty_m <= max_evaluable_uncertainty_m")
        ctx = None
        if raw.get("context_aware"):
            c = raw["context_aware"]
            ctx = ContextAwarePolicy(tuple(str(t).upper() for t in c.get("not_applicable_request_types", [])),
                                     " ".join(str(c.get("rationale", "")).split()))
        if mode == "CONTEXT_AWARE" and ctx is None:
            raise RulesetError("decision_mode CONTEXT_AWARE requires a context_aware section")
        ev = raw.get("evidence", {}) or {}
        evidence_categories = tuple(ev.get("nearest_overall_categories", list(rules)))
        return cls(
            ruleset_version=str(raw.get("ruleset_version", "0")),
            decision_mode=mode,
            metric_crs=str(raw.get("metric_crs", "EPSG:32118")),
            category_rules=rules,
            evidence_categories=tuple(dict.fromkeys(list(rules) + list(evidence_categories))),
            location=policy,
            context_aware=ctx,
            max_facilities_listed=int(ev.get("max_facilities_listed", 10)),
            include_duplicates=bool(ev.get("include_duplicates", False)),
            review_boundary_sensitive=bool(raw.get("review_boundary_sensitive", False)),
            request_catalog_path=raw.get("request_catalog_path"),
            osm_data_dir=raw.get("osm_data_dir"),
            provenance=raw.get("threshold_selection", {}) or {},
        )

    # ----------------------------------------------------------------------------------------
    @property
    def thresholds_m(self) -> dict:
        return {c: r.threshold_m for c, r in self.category_rules.items()}

    @property
    def max_threshold_m(self) -> float:
        return max(r.threshold_m for r in self.category_rules.values())

    def required_facility_buffer_m(self) -> float:
        """Minimum OSM facility buffer around NYC for the ruleset to be evaluated safely."""
        loc = self.location
        return self.max_threshold_m + loc.max_evaluable_uncertainty_m + loc.request_coverage_tolerance_m

    def rule_catalogue(self) -> dict:
        out = {r.rule_id: (r.description or f"A qualifying {c} facility lies within {r.threshold_m:g} m.")
               for c, r in self.category_rules.items()}
        out.update(STATIC_RULES)
        return out


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


# --------------------------------------------------------------------------------------------
# Decision logic (pure function of evidence; unit-testable without any spatial data)
# --------------------------------------------------------------------------------------------

@dataclass
class GeoDecision:
    value: Optional[bool]          # True / False / None (indeterminate)
    regime: str                    # POINT_ESTIMATE | ROBUST_INTERVAL
    best_margin_m: Optional[float]
    boundary_sensitive: bool
    incomplete_reason: Optional[str] = None


def decide_geographic(best_margin_m: Optional[float], uncertainty_m: float,
                      policy: LocationPolicy) -> GeoDecision:
    """Binary geographic decision from the best margin (threshold - distance) over qualifying facilities.

    best_margin_m is None when no qualifying facility lies within the search radius (threshold +
    uncertainty). In that case the margin is known to be below -uncertainty: a definite negative.

    * POINT_ESTIMATE regime (uncertainty <= point_decision_max_uncertainty_m):
        positive iff best_margin >= 0 (inclusive boundary, distance <= threshold).
    * ROBUST_INTERVAL regime (point limit < uncertainty <= max_evaluable_uncertainty_m):
        positive iff best_margin >= +u   (every possible true location is within the threshold)
        negative iff best_margin <  -u   (no possible true location is within the threshold)
        otherwise indeterminate (None).
    """
    u = max(0.0, float(uncertainty_m))
    if u <= policy.point_decision_max_uncertainty_m:
        if best_margin_m is None:
            return GeoDecision(False, "POINT_ESTIMATE", None, False)
        positive = best_margin_m >= -BOUNDARY_EPS_M
        return GeoDecision(positive, "POINT_ESTIMATE", best_margin_m, abs(best_margin_m) < u)
    if best_margin_m is None:
        return GeoDecision(False, "ROBUST_INTERVAL", None, False)
    if best_margin_m >= u - BOUNDARY_EPS_M:
        return GeoDecision(True, "ROBUST_INTERVAL", best_margin_m, False)
    if best_margin_m < -u:
        return GeoDecision(False, "ROBUST_INTERVAL", best_margin_m, False)
    return GeoDecision(None, "ROBUST_INTERVAL", best_margin_m, True, "LOCATION_UNCERTAINTY_SPANS_THRESHOLD")
