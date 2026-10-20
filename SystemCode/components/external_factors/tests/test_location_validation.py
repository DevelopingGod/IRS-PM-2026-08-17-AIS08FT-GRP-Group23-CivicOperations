"""Location validation and uncertainty scenarios (12-16) plus robustness of incomplete handling."""

import pytest

from conftest import req


def _incomplete(r, reason):
    assert r.external_factor is None, "incomplete evaluations must be null, never 0"
    assert r.evaluation_status == "INCOMPLETE"
    assert r.incomplete_reason == reason
    assert r.critical_facility_nearby is None
    assert r.needs_review is True


def test_12_missing_latitude(evaluator):
    r = evaluator.evaluate(req(1000, 920) | {"latitude": None})
    _incomplete(r, "MISSING_COORDINATES")
    assert "MISSING_FIELD:latitude" in r.warnings and r.triggered_rules == ["INPUT_MISSING_COORDINATES"]


def test_13_missing_longitude(evaluator):
    d = req(1000, 920)
    del d["longitude"]
    r = evaluator.evaluate(d)
    _incomplete(r, "MISSING_COORDINATES")
    assert "MISSING_FIELD:longitude" in r.warnings


@pytest.mark.parametrize("lat,lon,reason", [
    (95.0, -73.9, "INVALID_COORDINATES"),
    (40.7, -190.0, "INVALID_COORDINATES"),
    ("abc", -73.9, "INVALID_COORDINATES"),
    (float("inf"), -73.9, "INVALID_COORDINATES"),
    (0.0, 0.0, "INVALID_COORDINATES"),
    (float("nan"), -73.9, "MISSING_COORDINATES"),
    ("", -73.9, "MISSING_COORDINATES"),
    (True, -73.9, "INVALID_COORDINATES"),
])
def test_14_invalid_coordinate_ranges(evaluator, lat, lon, reason):
    _incomplete(evaluator.evaluate(req(1000, 920) | {"latitude": lat, "longitude": lon}), reason)


def test_14b_swapped_lat_lon_is_reported_not_repaired(evaluator):
    d = req(1000, 920)
    d["latitude"], d["longitude"] = d["longitude"], d["latitude"]
    r = evaluator.evaluate(d)
    _incomplete(r, "OUTSIDE_COVERAGE")
    assert "POSSIBLE_LAT_LON_SWAP" in r.warnings


def test_14c_numeric_strings_accepted(evaluator):
    d = req(1000, 920)
    d["latitude"], d["longitude"] = str(d["latitude"]), str(d["longitude"])
    assert evaluator.evaluate(d).external_factor == 1


@pytest.mark.parametrize("status", ["UNRESOLVED", "FAILED", "AMBIGUOUS", "NOT_ATTEMPTED"])
def test_15_unresolved_geolocation(evaluator, status):
    _incomplete(evaluator.evaluate(req(1000, 920, geolocation_status=status)), "GEOLOCATION_NOT_RESOLVED")


def test_15b_missing_geolocation_status(evaluator):
    d = req(1000, 920)
    del d["geolocation_status"]
    _incomplete(evaluator.evaluate(d), "MISSING_GEOLOCATION_STATUS")


def test_16_large_uncertainty_spanning_threshold_is_indeterminate(evaluator):
    # Nearest qualifying facility 10 m away, but +/-120 m (BLOCKFACE default) > threshold margin 90 m.
    d = req(1000, 1050, geolocation_status="APPROXIMATE", location_precision="BLOCKFACE")
    del d["location_uncertainty_m"]
    r = evaluator.evaluate(d)
    _incomplete(r, "LOCATION_UNCERTAINTY_SPANS_THRESHOLD")
    assert r.location_quality.uncertainty_m == 120 and r.location_quality.uncertainty_source == "DEFAULT_FOR_PRECISION"
    assert r.nearest_qualifying_facility is not None  # evidence still reported


def test_16b_robust_positive_and_negative(evaluator):
    pos = evaluator.evaluate(req(1000, 1030, geolocation_status="APPROXIMATE", location_uncertainty_m=60))
    assert pos.external_factor == 1 and pos.location_quality.decision_regime == "ROBUST_INTERVAL"
    assert "LOCATION_ROBUST_INTERVAL" in pos.triggered_rules
    neg = evaluator.evaluate(req(5000, 1000, geolocation_status="APPROXIMATE", location_uncertainty_m=60))
    assert neg.external_factor == 0 and neg.location_quality.decision_regime == "ROBUST_INTERVAL"


def test_16c_too_coarse_precision(evaluator):
    _incomplete(evaluator.evaluate(req(1000, 920, location_uncertainty_m=500)), "INSUFFICIENT_PRECISION")
    d = req(1000, 920, location_precision="POSTAL_CODE")
    del d["location_uncertainty_m"]
    _incomplete(evaluator.evaluate(d), "INSUFFICIENT_PRECISION")


def test_16d_point_estimate_boundary_sensitive_flag(evaluator):
    r = evaluator.evaluate(req(1000, 905, location_uncertainty_m=20))   # 95 m, margin 5 m < 20 m
    assert r.external_factor == 1
    assert r.location_quality.decision_regime == "POINT_ESTIMATE"
    assert r.location_quality.boundary_sensitive is True
    assert r.needs_review is False   # review only if review_boundary_sensitive is enabled


def test_unrecognised_precision_falls_back_to_unknown(evaluator):
    d = req(1000, 1030, location_precision="SOMETHING_ODD")
    del d["location_uncertainty_m"]
    r = evaluator.evaluate(d)
    assert "LOCATION_PRECISION_UNRECOGNISED:SOMETHING_ODD" in r.warnings
    assert r.location_quality.precision == "UNKNOWN" and r.location_quality.uncertainty_m == 100


def test_never_invents_coordinates(evaluator):
    d = req(1000, 920) | {"latitude": None, "zip_code": "10029", "original_location_text": "5th Ave & E 100 St"}
    r = evaluator.evaluate(d)
    assert r.external_factor is None and r.nearest_qualifying_facility is None
    assert r.location_quality.uncertainty_m is None
