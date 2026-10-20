"""Output contract, serialisation, batch behaviour and Urgency integration (25-27, 30)."""

import json

import pytest

from conftest import req
from civicops_external_factors import EvaluationResult, to_integer_factor
from civicops_external_factors.schemas import FORBIDDEN_OUTPUT_KEYS

REQUIRED_KEYS = {"request_id", "request_type", "external_factor", "evaluation_status", "incomplete_reason",
                 "critical_facility_nearby", "decision_mode", "thresholds_m", "nearest_qualifying_distance_m",
                 "nearest_qualifying_facility", "nearest_facility_overall", "triggering_facility_count",
                 "triggering_facilities", "triggering_facilities_truncated", "triggered_rules", "explanation",
                 "location_quality", "warnings", "needs_review", "ruleset_version", "osm_data_version",
                 "component_version"}


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


def test_25_single_request_evaluation(evaluator):
    r = evaluator.evaluate(req(1000, 920, request_id="R-1"))
    assert isinstance(r, EvaluationResult)
    d = r.to_dict()
    assert REQUIRED_KEYS <= set(d)
    assert d["request_id"] == "R-1" and d["external_factor"] in (0, 1)
    assert d["osm_data_version"] == "synthetic-test" and d["ruleset_version"] == "test"


def test_26_batch_evaluation_matches_single_and_preserves_order(evaluator):
    reqs = [req(1000, 920, request_id="a"), req(1000, 920, request_id="b") | {"latitude": None},
            req(5000, 1000, request_id="c"), req(1550, 550, request_id="d"),
            req(-300, 1000, request_id="e")]
    batch = evaluator.evaluate_batch(reqs)
    assert [r.request_id for r in batch] == ["a", "b", "c", "d", "e"]
    assert [r.external_factor for r in batch] == [1, None, 0, 1, None]
    for q, b in zip(reqs, batch):
        assert evaluator.evaluate(q).to_dict() == b.to_dict()
    assert evaluator.evaluate_batch([]) == []


def test_27_json_serialisation_round_trip(evaluator):
    for q in [req(1000, 920), req(1000, 920) | {"latitude": None}, req(5000, 1000)]:
        text = evaluator.evaluate(q).to_json()
        assert "NaN" not in text and "Infinity" not in text
        assert json.loads(text)["evaluation_status"] in ("COMPLETE", "INCOMPLETE")
    out = json.loads(evaluator.evaluate_json(json.dumps(req(1000, 920))))
    assert out["external_factor"] == 1


def test_30_urgency_payload_contains_only_factor_and_evidence(evaluator):
    p = evaluator.evaluate(req(1000, 920, request_id="U1")).to_urgency_payload()
    assert set(p) == {"request_id", "external_factor", "evaluation_status", "incomplete_reason",
                      "critical_facility_nearby", "triggered_rules", "evidence", "triggering_categories",
                      "explanation", "needs_review", "ruleset_version", "osm_data_version"}
    assert p["external_factor"] == 1 and p["evidence"]["facility_category"] == "SCHOOL"
    neg = evaluator.evaluate(req(5000, 1000)).to_urgency_payload()
    assert neg["external_factor"] == 0 and neg["evidence"] is None


def test_30b_no_urgency_priority_or_dispatch_fields_anywhere(evaluator):
    for q in [req(1000, 920), req(5000, 1000), req(1000, 920) | {"latitude": None}]:
        r = evaluator.evaluate(q)
        keys = set(all_keys(r.to_dict())) | set(all_keys(r.to_urgency_payload()))
        assert not keys & set(FORBIDDEN_OUTPUT_KEYS)
        assert "urgen" not in " ".join(keys) and "priorit" not in " ".join(keys)


def test_null_is_never_silently_zero(evaluator):
    r = evaluator.evaluate(req(1000, 920) | {"latitude": None})
    assert r.to_dict()["external_factor"] is None
    with pytest.raises(ValueError, match="null_policy"):
        to_integer_factor(r)
    z = to_integer_factor(r, "ZERO_FLAGGED")
    assert z == {"external_factor": 0, "imputed": True, "null_policy": "ZERO_FLAGGED",
                 "incomplete_reason": "MISSING_COORDINATES"}
    ok = to_integer_factor(evaluator.evaluate(req(1000, 920)))
    assert ok == {"external_factor": 1, "imputed": False, "null_policy": None}


def test_distinguishes_nearest_overall_qualifying_and_triggering(evaluator):
    d = evaluator.evaluate(req(530, 1450)).to_dict()   # transit 58 m, nothing qualifying within 100 m
    assert d["nearest_facility_overall"]["facility_category"] == "TRANSIT_STATION"
    assert d["nearest_qualifying_facility"]["qualifies"] is True
    assert d["triggering_facilities"] == [] and d["triggering_facility_count"] == 0


def test_deterministic(evaluator):
    q = req(1000, 1030)
    assert evaluator.evaluate(q).to_json() == evaluator.evaluate(q).to_json()


def test_leakage_fields_are_ignored(evaluator):
    q = req(1000, 920)
    leaky = q | {"closed_date": "2026-01-01", "outcome_class": "UNFOUNDED", "resolution_hours": 3.0}
    a, b = evaluator.evaluate(q).to_dict(), evaluator.evaluate(leaky).to_dict()
    assert any(w.startswith("AFTER_THE_FACT_FIELDS_IGNORED") for w in b["warnings"])
    for k in ("external_factor", "triggering_facilities", "triggered_rules"):
        assert a[k] == b[k]


def test_optional_future_intake_fields_are_accepted_not_required(evaluator):
    q = req(1000, 920) | {"address_type": "INTERSECTION", "cross_street_2": "E 101 St", "zip_code": "10029",
                          "original_location_text": "near the school", "citizen_gps": {"lat": 1, "lon": 2},
                          "slots": {"borough": "MANHATTAN", "incident_address": "x"}}
    r = evaluator.evaluate(q)
    assert r.external_factor == 1 and r.location_quality.borough_supplied == "MANHATTAN"


def test_intake_passthrough_flags_do_not_change_factor(evaluator):
    q = req(1000, 920) | {"emergency_action": "STOP_CALL_911", "needs_human": True, "ready_to_submit": False}
    r = evaluator.evaluate(q)
    assert r.external_factor == 1
    assert {"NEEDS_HUMAN_TRUE", "READY_TO_SUBMIT_FALSE"} <= set(r.warnings)


def test_borough_mismatch_warning(evaluator):
    r = evaluator.evaluate(req(1000, 920, borough="QUEENS"))
    assert any(w.startswith("BOROUGH_MISMATCH") for w in r.warnings) and r.external_factor == 1


def test_truncation_of_listed_facilities(synth_dir, tmp_path):
    from conftest import write_config
    from civicops_external_factors import CriticalFacilityEvaluator
    ev = CriticalFacilityEvaluator.from_config(write_config(tmp_path / "r.yaml"), osm_data_dir=synth_dir,
                                               overrides={"evidence": {"max_facilities_listed": 1}})
    r = ev.evaluate(req(1000, 1030))
    assert r.triggering_facility_count == 2 and len(r.triggering_facilities) == 1
    assert r.triggering_facilities_truncated is True
