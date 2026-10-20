"""Request-context validation: coordinates, geolocation status, precision and request type.

Validation never invents or repairs coordinates. Swapped latitude/longitude, for example, is
reported but not corrected. Anything the component cannot evaluate safely gives an explicit
incomplete reason.
"""

from __future__ import annotations

import csv
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .proximity_rules import LocationPolicy
from .schemas import BOROUGHS, GEOLOCATION_STATUSES, LOCATION_PRECISIONS, RequestContext, is_finite_number

RT_PATTERN = re.compile(r"^RT\d{3}$")
NON_RT_LABELS = ("EMERGENCY", "OTHER", "SMALLTALK")


@dataclass
class RequestCatalog:
    """Read-only view of data/reference/request_catalog.csv (the shared request-type key)."""

    families: dict  # request_type_code -> complaint_family
    labels: dict    # request_type_code -> citizen_label

    @classmethod
    def from_csv(cls, path: str | Path) -> "RequestCatalog":
        families, labels = {}, {}
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                code = (row.get("request_type_code") or "").strip()
                if code:
                    families[code] = (row.get("complaint_family") or "").strip()
                    labels[code] = (row.get("citizen_label") or "").strip()
        if not families:
            raise ValueError(f"Request catalog {path} contains no request types")
        return cls(families, labels)

    def __contains__(self, code: object) -> bool:
        return code in self.families


@dataclass
class ValidatedLocation:
    ok: bool
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    status: Optional[str] = None
    precision: Optional[str] = None
    uncertainty_m: Optional[float] = None
    uncertainty_source: Optional[str] = None
    regime: Optional[str] = None
    incomplete_reason: Optional[str] = None
    rule_id: Optional[str] = None
    warnings: list = field(default_factory=list)


def normalise_precision(raw: Optional[str], policy: LocationPolicy) -> tuple[str, list]:
    warnings = []
    if raw is None or str(raw).strip() == "":
        return "UNKNOWN", ["LOCATION_PRECISION_MISSING"]
    p = str(raw).strip().upper().replace(" ", "_")
    p = policy.precision_aliases.get(p, p)
    if p not in LOCATION_PRECISIONS:
        warnings.append(f"LOCATION_PRECISION_UNRECOGNISED:{raw}")
        p = "UNKNOWN"
    return p, warnings


def validate_location(ctx: RequestContext, policy: LocationPolicy) -> ValidatedLocation:
    """Check coordinates, geolocation status and precision. Coverage is checked by the evaluator."""
    warnings: list = []
    status = None if ctx.geolocation_status is None else str(ctx.geolocation_status).strip().upper()
    precision, pw = normalise_precision(ctx.location_precision, policy)
    warnings += pw

    lat_missing = ctx.latitude is None or (isinstance(ctx.latitude, str) and not ctx.latitude.strip())
    lon_missing = ctx.longitude is None or (isinstance(ctx.longitude, str) and not ctx.longitude.strip())
    if not lat_missing and isinstance(ctx.latitude, float) and math.isnan(ctx.latitude):
        lat_missing = True
    if not lon_missing and isinstance(ctx.longitude, float) and math.isnan(ctx.longitude):
        lon_missing = True
    base = dict(status=status, precision=precision)
    if lat_missing or lon_missing:
        missing = [n for n, m in (("latitude", lat_missing), ("longitude", lon_missing)) if m]
        return ValidatedLocation(False, **base, incomplete_reason="MISSING_COORDINATES",
                                 rule_id="INPUT_MISSING_COORDINATES",
                                 warnings=warnings + [f"MISSING_FIELD:{f}" for f in missing])
    if not (is_finite_number(ctx.latitude) and is_finite_number(ctx.longitude)):
        return ValidatedLocation(False, **base, incomplete_reason="INVALID_COORDINATES",
                                 rule_id="INPUT_INVALID_COORDINATES", warnings=warnings + ["NON_NUMERIC_COORDINATES"])
    lat, lon = float(ctx.latitude), float(ctx.longitude)
    if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
        if -90.0 <= lon <= 90.0 and -180.0 <= lat <= 180.0:
            warnings.append("POSSIBLE_LAT_LON_SWAP")
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                 incomplete_reason="INVALID_COORDINATES", rule_id="INPUT_INVALID_COORDINATES",
                                 warnings=warnings + ["COORDINATES_OUT_OF_RANGE"])
    if 35 < lon < 45 and -80 < lat < -70:  # within range but looks like NYC with axes swapped
        warnings.append("POSSIBLE_LAT_LON_SWAP")
    if lat == 0.0 and lon == 0.0:
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                 incomplete_reason="INVALID_COORDINATES", rule_id="INPUT_INVALID_COORDINATES",
                                 warnings=warnings + ["NULL_ISLAND_COORDINATES"])

    if status is None:
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                 incomplete_reason="MISSING_GEOLOCATION_STATUS",
                                 rule_id="INPUT_MISSING_GEOLOCATION_STATUS", warnings=warnings)
    if status not in GEOLOCATION_STATUSES:
        warnings.append(f"GEOLOCATION_STATUS_UNRECOGNISED:{ctx.geolocation_status}")
    if status not in policy.accepted_geolocation_status:
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                 incomplete_reason="GEOLOCATION_NOT_RESOLVED",
                                 rule_id="INPUT_GEOLOCATION_NOT_RESOLVED", warnings=warnings)
    if precision in policy.unsupported_precision:
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                 incomplete_reason="INSUFFICIENT_PRECISION",
                                 rule_id="INPUT_INSUFFICIENT_PRECISION", warnings=warnings)

    # Effective uncertainty: supplied value wins; otherwise the documented default for the precision.
    if ctx.location_uncertainty_m is not None and is_finite_number(ctx.location_uncertainty_m) \
            and float(ctx.location_uncertainty_m) >= 0:
        u, usrc = float(ctx.location_uncertainty_m), "SUPPLIED"
    else:
        if ctx.location_uncertainty_m is not None:
            warnings.append("LOCATION_UNCERTAINTY_INVALID_IGNORED")
        if precision not in policy.default_uncertainty_m:
            return ValidatedLocation(False, **base, latitude=lat, longitude=lon,
                                     incomplete_reason="INSUFFICIENT_PRECISION",
                                     rule_id="INPUT_INSUFFICIENT_PRECISION",
                                     warnings=warnings + ["NO_DEFAULT_UNCERTAINTY_FOR_PRECISION"])
        u, usrc = policy.default_uncertainty_m[precision], "DEFAULT_FOR_PRECISION"
    if status == "APPROXIMATE" and usrc == "DEFAULT_FOR_PRECISION" and precision in ("ADDRESS_POINT", "GPS_DEVICE"):
        warnings.append("APPROXIMATE_STATUS_WITH_PRECISE_PRECISION")
    if u > policy.max_evaluable_uncertainty_m:
        return ValidatedLocation(False, **base, latitude=lat, longitude=lon, uncertainty_m=u,
                                 uncertainty_source=usrc, incomplete_reason="INSUFFICIENT_PRECISION",
                                 rule_id="INPUT_INSUFFICIENT_PRECISION", warnings=warnings)
    regime = "POINT_ESTIMATE" if u <= policy.point_decision_max_uncertainty_m else "ROBUST_INTERVAL"
    return ValidatedLocation(True, lat, lon, status, precision, u, usrc, regime, warnings=warnings)


def request_type_warnings(request_type: Optional[str], catalog: Optional[RequestCatalog]) -> tuple[bool, list]:
    """Return (is_known_catalog_type, warnings). The request type never changes the spatial evidence."""
    if request_type is None or str(request_type).strip() == "":
        return False, ["REQUEST_TYPE_MISSING"]
    rt = str(request_type).strip().upper()
    if rt in NON_RT_LABELS:
        return False, [f"REQUEST_TYPE_IS_NON_DISPATCH_LABEL:{rt}"]
    if not RT_PATTERN.match(rt):
        return False, [f"REQUEST_TYPE_MALFORMED:{request_type}"]
    if catalog is not None and rt not in catalog:
        return False, [f"REQUEST_TYPE_NOT_IN_CATALOG:{rt}"]
    return True, []


def context_warnings(ctx: RequestContext) -> list:
    """Pass-through checks on intake/emergency fields. They are recorded but never alter the factor."""
    w = []
    if ctx.emergency_action is not None and str(ctx.emergency_action).upper() != "CONTINUE":
        w.append(f"EMERGENCY_ACTION_NOT_CONTINUE:{ctx.emergency_action}")
    if ctx.needs_human is True:
        w.append("NEEDS_HUMAN_TRUE")
    if ctx.ready_to_submit is False:
        w.append("READY_TO_SUBMIT_FALSE")
    if ctx.borough is not None and str(ctx.borough).upper() not in BOROUGHS:
        w.append(f"BOROUGH_UNRECOGNISED:{ctx.borough}")
    leaked = ctx.leakage_fields_present()
    if leaked:
        w.append("AFTER_THE_FACT_FIELDS_IGNORED:" + ",".join(leaked))
    return w
