"""CriticalFacilityEvaluator: the public entry point of the External Factors component.

    from civicops_external_factors import CriticalFacilityEvaluator
    evaluator = CriticalFacilityEvaluator.from_config("config/proximity_rules.yaml")
    result = evaluator.evaluate(request_context)
    result.to_dict()              # full evidence record
    result.to_urgency_payload()   # minimal hand-off for the Urgency component

The evaluator separates SPATIAL EVIDENCE (which facilities are nearby and how far away) from
the BINARY DECISION (does any qualifying facility meet its threshold). It produces no urgency
level, score, weight or priority.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

from . import __version__
from .explanations import explain_incomplete, explain_negative, explain_not_applicable, explain_positive
from .osm_loader import FacilityCache, OSMCacheError, load_facility_cache
from .proximity_rules import BOUNDARY_EPS_M, ProximityRuleset, decide_geographic
from .schemas import EvaluationResult, FacilityEvidence, LocationQuality, RequestContext
from .spatial_engine import SpatialEngine
from .validators import (RequestCatalog, ValidatedLocation, context_warnings, request_type_warnings,
                         validate_location)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
# Candidate search slack (1 mm) so that projection round-off cannot drop a facility lying exactly
# at the threshold before the inclusive decision test (distance <= threshold + BOUNDARY_EPS_M).
SEARCH_SLACK_M = 1e-3
DEFAULT_CONFIG = PACKAGE_ROOT / "config" / "proximity_rules.yaml"


def _resolve(path_value: Optional[str], base: Path) -> Optional[Path]:
    if path_value is None:
        return None
    p = Path(path_value)
    return p if p.is_absolute() else (base / p).resolve()


class CriticalFacilityEvaluator:
    def __init__(self, ruleset: ProximityRuleset, cache: FacilityCache,
                 catalog: Optional[RequestCatalog] = None):
        self.ruleset = ruleset
        self.cache = cache
        self.catalog = catalog
        required = ruleset.required_facility_buffer_m()
        if cache.facility_buffer_m + 1e-9 < required:
            raise OSMCacheError(
                f"OSM facility buffer ({cache.facility_buffer_m:g} m) is smaller than the ruleset needs "
                f"({required:g} m = max threshold + max evaluable uncertainty + coverage tolerance). "
                f"Re-run data preparation with a larger facility_buffer_m.")
        cached_cats = set(cache.facilities["category"].unique())
        absent = [c for c in ruleset.category_rules if c not in cached_cats]
        if absent:
            raise OSMCacheError(f"Ruleset categories absent from the OSM cache: {absent}")
        if ruleset.decision_mode == "CONTEXT_AWARE" and catalog is None:
            raise ValueError("CONTEXT_AWARE mode requires the request catalog")
        self.engine = SpatialEngine(cache.facilities, cache.boroughs, ruleset.metric_crs,
                                    categories=ruleset.evidence_categories,
                                    include_duplicates=ruleset.include_duplicates)
        fac = self.engine.facilities
        self._cols = {col: fac[col].tolist() for col in
                      ("facility_id", "osm_type", "osm_id", "category", "name", "geometry_type", "borough")
                      if col in fac.columns}

    # ------------------------------------------------------------------------------------------
    @classmethod
    def from_config(cls, config_path: str | Path = DEFAULT_CONFIG, osm_data_dir: str | Path | None = None,
                    request_catalog_path: str | Path | None = None,
                    overrides: Optional[dict] = None) -> "CriticalFacilityEvaluator":
        """Build an evaluator from a YAML ruleset. Relative paths in the YAML resolve against its folder."""
        config_path = Path(config_path).resolve()
        ruleset = ProximityRuleset.from_yaml(config_path, overrides=overrides)
        base = config_path.parent
        data_dir = Path(osm_data_dir) if osm_data_dir else _resolve(ruleset.osm_data_dir or "../data/osm", base)
        cache = load_facility_cache(data_dir)
        cat_path = Path(request_catalog_path) if request_catalog_path else _resolve(ruleset.request_catalog_path, base)
        catalog = RequestCatalog.from_csv(cat_path) if cat_path and Path(cat_path).exists() else None
        return cls(ruleset, cache, catalog)

    # ------------------------------------------------------------------------------------------
    def evaluate(self, request: dict | RequestContext) -> EvaluationResult:
        return self.evaluate_batch([request])[0]

    def evaluate_json(self, text: str) -> str:
        return self.evaluate(json.loads(text)).to_json()

    def evaluate_batch(self, requests: Iterable[dict | RequestContext]) -> list[EvaluationResult]:
        rs = self.ruleset
        ctxs = [r if isinstance(r, RequestContext) else RequestContext.from_dict(r) for r in requests]
        n = len(ctxs)
        vlocs = [validate_location(c, rs.location) for c in ctxs]
        rt_info = [request_type_warnings(c.request_type, self.catalog) for c in ctxs]
        results: list[Optional[EvaluationResult]] = [None] * n

        valid = [i for i, v in enumerate(vlocs) if v.ok]
        if valid:
            pts = self.engine.project([vlocs[i].latitude for i in valid], [vlocs[i].longitude for i in valid])
            covered = self.engine.in_coverage(pts, rs.location.request_coverage_tolerance_m)
            boroughs = self.engine.borough_of(pts)
            u = np.array([vlocs[i].uncertainty_m for i in valid], dtype=float)

            # Spatial evidence 1: qualifying candidates within threshold + uncertainty, per category.
            cand: list[list] = [[] for _ in valid]
            for cat, rule in rs.category_rules.items():
                p_idx, rows, d = self.engine.within(pts, cat, rule.threshold_m + u + SEARCH_SLACK_M)
                for p, r, dist in zip(p_idx, rows, d):
                    cand[p].append((int(r), float(dist), rule.threshold_m))
            # Spatial evidence 2: nearest facility per evidence category (unbounded).
            nearest = {c: self.engine.nearest(pts, c) for c in rs.evidence_categories}

            for k, i in enumerate(valid):
                ctx, v = ctxs[i], vlocs[i]
                if not covered[k]:
                    v = ValidatedLocation(False, v.latitude, v.longitude, v.status, v.precision,
                                          v.uncertainty_m, v.uncertainty_source, v.regime,
                                          "OUTSIDE_COVERAGE", "INPUT_OUTSIDE_COVERAGE", v.warnings)
                    results[i] = self._incomplete(ctx, v, rt_info[i][1])
                    continue
                results[i] = self._decide(ctx, v, cand[k], {c: (nearest[c][0][k], nearest[c][1][k])
                                                             for c in nearest}, boroughs[k], rt_info[i])
        for i in range(n):
            if results[i] is None:
                results[i] = self._incomplete(ctxs[i], vlocs[i], rt_info[i][1])
        return results  # type: ignore[return-value]

    # ------------------------------------------------------------------------------------------
    def _evidence(self, row: int, distance: float, qualifies: bool) -> FacilityEvidence:
        col = self._cols
        cat = str(col["category"][row])
        thr = self.ruleset.category_rules[cat].threshold_m if qualifies and cat in self.ruleset.category_rules else None

        def clean(x):
            return None if x is None or (isinstance(x, float) and math.isnan(x)) else str(x)
        return FacilityEvidence(
            facility_id=str(col["facility_id"][row]), osm_type=str(col["osm_type"][row]),
            osm_id=int(col["osm_id"][row]), facility_category=cat, name=clean(col["name"][row]),
            distance_m=float(distance), geometry_type=str(col["geometry_type"][row]), threshold_m=thr,
            margin_m=None if thr is None else thr - float(distance), qualifies=thr is not None,
            borough=clean(col.get("borough", [None] * (row + 1))[row]))

    def _base_warnings(self, ctx: RequestContext, v: ValidatedLocation, rt_warn: list) -> list:
        return list(dict.fromkeys(v.warnings + rt_warn + context_warnings(ctx)))

    def _meta(self, ctx: RequestContext) -> dict:
        return dict(request_id=ctx.request_id, request_type=ctx.request_type,
                    ruleset_version=self.ruleset.ruleset_version, osm_data_version=self.cache.data_version,
                    component_version=__version__)

    def _incomplete(self, ctx: RequestContext, v: ValidatedLocation, rt_warn: list,
                    nearest_q: Optional[FacilityEvidence] = None, nearest_all: Optional[FacilityEvidence] = None,
                    lq: Optional[LocationQuality] = None, detail: str = "") -> EvaluationResult:
        lq = lq or LocationQuality(status=v.status, precision=v.precision, source=ctx.location_source,
                                   uncertainty_m=v.uncertainty_m, uncertainty_source=v.uncertainty_source,
                                   decision_regime=None, borough_supplied=ctx.borough)
        return EvaluationResult(
            external_factor=None, evaluation_status="INCOMPLETE", incomplete_reason=v.incomplete_reason,
            critical_facility_nearby=None, decision_mode=self.ruleset.decision_mode,
            thresholds_m=self.ruleset.thresholds_m, nearest_facility_overall=nearest_all,
            nearest_qualifying_facility=nearest_q, triggering_facility_count=0, triggering_facilities=[],
            triggering_facilities_truncated=False, triggered_rules=[v.rule_id] if v.rule_id else [],
            explanation=explain_incomplete(v.incomplete_reason or "UNKNOWN", detail), location_quality=lq,
            warnings=self._base_warnings(ctx, v, rt_warn), needs_review=True, **self._meta(ctx))

    def _decide(self, ctx: RequestContext, v: ValidatedLocation, candidates: list, nearest: dict,
                borough_from_coords: Optional[str], rt_info: tuple) -> EvaluationResult:
        rs = self.ruleset
        rt_known, rt_warn = rt_info
        warnings_extra = []
        if ctx.borough and borough_from_coords and str(ctx.borough).upper() != borough_from_coords:
            warnings_extra.append(f"BOROUGH_MISMATCH:supplied={ctx.borough},coordinates={borough_from_coords}")

        # Nearest overall (any cached evidence category) and nearest qualifying (ruleset categories).
        def best(cats):
            opts = [(nearest[c][1], nearest[c][0], c) for c in cats if c in nearest and nearest[c][0] >= 0]
            return min(opts) if opts else None
        b_all = best(rs.evidence_categories)
        b_q = best(rs.category_rules)
        nearest_all = None if b_all is None else self._evidence(b_all[1], b_all[0], b_all[2] in rs.category_rules)
        nearest_q = None if b_q is None else self._evidence(b_q[1], b_q[0], True)

        # Binary geographic decision from the best margin.
        best_margin = max((t - d for _, d, t in candidates), default=None)
        geo = decide_geographic(best_margin, v.uncertainty_m or 0.0, rs.location)
        if best_margin is None and nearest_q is not None:
            margin_for_report = nearest_q.margin_m
        else:
            margin_for_report = best_margin
        lq = LocationQuality(status=v.status, precision=v.precision, source=ctx.location_source,
                             uncertainty_m=v.uncertainty_m, uncertainty_source=v.uncertainty_source,
                             decision_regime=geo.regime, decision_margin_m=margin_for_report,
                             boundary_sensitive=geo.boundary_sensitive, borough_supplied=ctx.borough,
                             borough_from_coordinates=borough_from_coords)
        if geo.value is None:
            v2 = ValidatedLocation(False, v.latitude, v.longitude, v.status, v.precision, v.uncertainty_m,
                                   v.uncertainty_source, v.regime, geo.incomplete_reason,
                                   "INPUT_UNCERTAINTY_SPANS_THRESHOLD", v.warnings + warnings_extra)
            detail = (f"Best margin {best_margin:+.0f} m versus uncertainty +/-{v.uncertainty_m:.0f} m; a more "
                      f"precise location is needed.")
            return self._incomplete(ctx, v2, rt_warn, nearest_q, nearest_all, lq, detail)

        # Facilities that actually trigger a rule (point-estimate distance within their threshold).
        seen, trig = set(), []
        for row, d, t in sorted(candidates, key=lambda x: x[1]):
            if d <= t + BOUNDARY_EPS_M:
                ev = self._evidence(row, d, True)
                if ev.facility_id not in seen:
                    seen.add(ev.facility_id)
                    trig.append(ev)
        if not geo.value:
            trig = []
        regime_rule = "LOCATION_POINT_ESTIMATE" if geo.regime == "POINT_ESTIMATE" else "LOCATION_ROBUST_INTERVAL"
        if geo.value:
            cat_order = list(rs.category_rules)
            cats = sorted({f.facility_category for f in trig}, key=cat_order.index)
            rules = [rs.category_rules[c].rule_id for c in cats] + [regime_rule]
            explanation = explain_positive(trig, geo.regime, v.uncertainty_m)
        else:
            rules = ["CRITICAL_PROXIMITY_NONE", regime_rule]
            explanation = explain_negative(nearest_q, geo.regime, v.uncertainty_m)

        nearby = bool(geo.value)
        factor: Optional[int] = int(nearby)
        status, reason = "COMPLETE", None
        if rs.decision_mode == "CONTEXT_AWARE":
            rt = str(ctx.request_type).strip().upper() if rt_known else None
            if rt is None:
                factor, status, reason = None, "INCOMPLETE", "UNKNOWN_REQUEST_TYPE"
                rules = rules + ["INPUT_UNKNOWN_REQUEST_TYPE"]
                explanation = explain_incomplete(reason, "critical_facility_nearby still reports the "
                                                 "pure geographic evidence.")
            elif rt in rs.context_aware.not_applicable_request_types:
                factor = 0
                rules = rules + ["CONTEXT_NOT_APPLICABLE"]
                explanation = (explain_not_applicable(rt, self.catalog.labels.get(rt, ""))
                               + " Geographic evidence: " + explanation)

        listed = trig[: rs.max_facilities_listed]
        needs_review = status != "COMPLETE" or (geo.boundary_sensitive and rs.review_boundary_sensitive)
        return EvaluationResult(
            external_factor=factor, evaluation_status=status, incomplete_reason=reason,
            critical_facility_nearby=nearby, decision_mode=rs.decision_mode, thresholds_m=rs.thresholds_m,
            nearest_facility_overall=nearest_all, nearest_qualifying_facility=nearest_q,
            triggering_facility_count=len(trig), triggering_facilities=listed,
            triggering_facilities_truncated=len(trig) > len(listed), triggered_rules=rules,
            explanation=explanation, location_quality=lq,
            warnings=list(dict.fromkeys(self._base_warnings(ctx, v, rt_warn) + warnings_extra)),
            needs_review=needs_review, **self._meta(ctx))


# ----------------------------------------------------------------------------------------------
# Optional adapter for an integer-only integration contract
# ----------------------------------------------------------------------------------------------

NULL_POLICIES = ("ZERO_FLAGGED", "ONE_FLAGGED")


def to_integer_factor(result: EvaluationResult | dict, null_policy: Optional[str] = None) -> dict:
    """Convert to an always-integer factor ONLY under an explicitly agreed null policy.

    No default policy is assumed: calling this with an incomplete result and no policy raises,
    so missing data is never silently turned into 0. Each imputed value is flagged.
    """
    d = result.to_dict() if isinstance(result, EvaluationResult) else result
    if d["external_factor"] is not None:
        return {"external_factor": int(d["external_factor"]), "imputed": False, "null_policy": None}
    if null_policy not in NULL_POLICIES:
        raise ValueError(f"Incomplete evaluation ({d.get('incomplete_reason')}); an agreed null_policy "
                         f"from {NULL_POLICIES} is required to produce an integer.")
    return {"external_factor": 0 if null_policy == "ZERO_FLAGGED" else 1, "imputed": True,
            "null_policy": null_policy, "incomplete_reason": d.get("incomplete_reason")}
