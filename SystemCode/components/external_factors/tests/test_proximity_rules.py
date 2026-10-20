"""Ruleset configuration, decision logic and request-type handling (21-24)."""

import pytest

from conftest import req, write_config
from civicops_external_factors import CriticalFacilityEvaluator, ProximityRuleset, RulesetError
from civicops_external_factors.proximity_rules import LocationPolicy, decide_geographic

POLICY = LocationPolicy(("RESOLVED",), {}, {"ADDRESS_POINT": 15}, (), 30.0, 250.0, 100.0)


# ------------------------------------------------------------------ pure decision logic
@pytest.mark.parametrize("margin,u,expected,regime", [
    (0.0, 0, True, "POINT_ESTIMATE"),         # exactly at threshold (inclusive)
    (-0.01, 0, False, "POINT_ESTIMATE"),
    (5.0, 20, True, "POINT_ESTIMATE"),
    (None, 15, False, "POINT_ESTIMATE"),      # no candidate within threshold + u
    (60.0, 60, True, "ROBUST_INTERVAL"),      # d + u == T  -> robust positive (inclusive)
    (-61.0, 60, False, "ROBUST_INTERVAL"),
    (10.0, 60, None, "ROBUST_INTERVAL"),      # straddles -> indeterminate
    (-60.0, 60, None, "ROBUST_INTERVAL"),     # d - u == T is still "possibly within"
])
def test_decide_geographic(margin, u, expected, regime):
    g = decide_geographic(margin, u, POLICY)
    assert g.value is expected and g.regime == regime


# ------------------------------------------------------------------ request type (no double counting)
def test_request_type_does_not_change_pure_geographic_result(evaluator):
    a = evaluator.evaluate(req(1000, 920, request_type="RT005")).to_dict()
    b = evaluator.evaluate(req(1000, 920, request_type="RT080")).to_dict()
    for k in ("external_factor", "critical_facility_nearby", "triggering_facilities", "triggered_rules"):
        assert a[k] == b[k]


def test_21_unknown_request_type_pure_mode_completes_with_warning(evaluator):
    r = evaluator.evaluate(req(1000, 920, request_type="FOO"))
    assert r.external_factor == 1 and r.evaluation_status == "COMPLETE"
    assert "REQUEST_TYPE_MALFORMED:FOO" in r.warnings
    r2 = evaluator.evaluate(req(1000, 920, request_type=None))
    assert r2.external_factor == 1 and "REQUEST_TYPE_MISSING" in r2.warnings
    r3 = evaluator.evaluate(req(1000, 920, request_type="OTHER"))
    assert "REQUEST_TYPE_IS_NON_DISPATCH_LABEL:OTHER" in r3.warnings


def test_22_request_type_absent_from_catalog(evaluator):
    r = evaluator.evaluate(req(1000, 920, request_type="RT999"))
    assert r.external_factor == 1 and "REQUEST_TYPE_NOT_IN_CATALOG:RT999" in r.warnings


def test_context_aware_mode(synth_dir, tmp_path):
    ev = CriticalFacilityEvaluator.from_config(write_config(tmp_path / "c.yaml", mode="CONTEXT_AWARE"),
                                               osm_data_dir=synth_dir)
    street = ev.evaluate(req(1000, 920, request_type="RT005"))
    assert street.external_factor == 1
    interior = ev.evaluate(req(1000, 920, request_type="RT080"))
    assert interior.external_factor == 0 and interior.critical_facility_nearby is True
    assert "CONTEXT_NOT_APPLICABLE" in interior.triggered_rules
    unknown = ev.evaluate(req(1000, 920, request_type="RT999"))
    assert unknown.external_factor is None and unknown.incomplete_reason == "UNKNOWN_REQUEST_TYPE"
    assert unknown.critical_facility_nearby is True   # pure geographic evidence kept separate


# ------------------------------------------------------------------ configuration
def test_23_threshold_configuration_change(synth_dir, tmp_path):
    base = CriticalFacilityEvaluator.from_config(write_config(tmp_path / "a.yaml", threshold=100), osm_data_dir=synth_dir)
    assert base.evaluate(req(1000, 880)).external_factor == 0           # 120 m
    wide = CriticalFacilityEvaluator.from_config(write_config(tmp_path / "b.yaml", threshold=150), osm_data_dir=synth_dir)
    r = wide.evaluate(req(1000, 880))
    assert r.external_factor == 1 and r.thresholds_m["SCHOOL"] == 150
    narrow = CriticalFacilityEvaluator.from_config(tmp_path / "a.yaml", osm_data_dir=synth_dir,
                                                   overrides={"facility_categories": {"SCHOOL": {"threshold_m": 50}}})
    assert narrow.evaluate(req(1000, 920)).external_factor == 0         # 80 m > 50 m
    assert narrow.ruleset.thresholds_m["SCHOOL"] == 50 and narrow.ruleset.thresholds_m["HOSPITAL"] == 100


@pytest.mark.parametrize("bad", [0, -10, "abc", None, float("nan"), float("inf"), True])
def test_24_threshold_zero_or_invalid_rejected(tmp_path, bad):
    cfg = write_config(tmp_path / "r.yaml")
    with pytest.raises(RulesetError):
        ProximityRuleset.from_yaml(cfg, overrides={"facility_categories": {"SCHOOL": {"threshold_m": bad}}})


def test_24b_other_invalid_configurations(tmp_path):
    cfg = write_config(tmp_path / "r.yaml")
    with pytest.raises(RulesetError):
        ProximityRuleset.from_yaml(cfg, overrides={"decision_mode": "URGENCY"})
    with pytest.raises(RulesetError):
        ProximityRuleset.from_yaml(cfg, overrides={"location": {"max_evaluable_uncertainty_m": 10}})
    with pytest.raises(RulesetError):
        ProximityRuleset.from_yaml(tmp_path / "missing.yaml")
    disabled = {c: {"threshold_m": 100, "enabled": False} for c in
                ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"]}
    with pytest.raises(RulesetError):
        ProximityRuleset.from_yaml(cfg, overrides={"facility_categories": disabled})


def test_category_specific_thresholds(synth_dir, tmp_path):
    cfg = write_config(tmp_path / "r.yaml")
    ev = CriticalFacilityEvaluator.from_config(cfg, osm_data_dir=synth_dir, overrides={
        "facility_categories": {"HOSPITAL": {"threshold_m": 200}}})
    r = ev.evaluate(req(1750, 550))                  # 150 m from hospital polygon
    assert r.external_factor == 1 and r.triggering_facilities[0].threshold_m == 200


def test_real_config_is_pure_geographic_and_frozen():
    from conftest import REAL_CONFIG
    rs = ProximityRuleset.from_yaml(REAL_CONFIG)
    assert rs.decision_mode == "PURE_GEOGRAPHIC"
    assert set(rs.thresholds_m) == {"HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"}
    assert rs.provenance.get("selection_split") == "val"
