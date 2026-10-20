"""Business rules R01-R14, one behaviour per test, positive and negative cases."""

from __future__ import annotations

import itertools
import json

import pytest

from civicops_agency import RULES, AgencySkillRouter, RouterConfig
from civicops_agency.simulator import make_co1

from conftest import CATALOG_PATH, KNOWLEDGE_PATH


def codes(result):
    return [f.code for f in result.findings]


# ---------------------------------------------------------------- register
def test_rule_register_is_complete_and_ordered():
    assert list(RULES) == [f"R{i:02d}" for i in range(1, 15)]
    assert all(r.statement for r in RULES.values())


# ---------------------------------------------------------------- R01
@pytest.mark.parametrize("override, code", [
    ({"drop": ["request_type"]}, "MISSING_REQUEST_TYPE"),
    ({"request_type": ""}, "MISSING_REQUEST_TYPE"),
    ({"request_type": 22}, "INVALID_REQUEST_TYPE_TYPE"),
    ({"request_type": "RT22"}, "MALFORMED_REQUEST_TYPE"),
    ({"confidence": 1.01}, "CONFIDENCE_OUT_OF_RANGE"),
    ({"confidence": -0.1}, "CONFIDENCE_OUT_OF_RANGE"),
    ({"confidence": float("nan")}, "CONFIDENCE_OUT_OF_RANGE"),
    ({"confidence": True}, "INVALID_CONFIDENCE_TYPE"),
    ({"confidence": "0.9"}, "INVALID_CONFIDENCE_TYPE"),
    ({"drop": ["confidence"]}, "MISSING_CONFIDENCE"),
    ({"ready_to_submit": "true"}, "INVALID_READY_TO_SUBMIT_TYPE"),
    ({"needs_human": 0}, "INVALID_NEEDS_HUMAN_TYPE"),
    ({"drop": ["is_emergency"]}, "MISSING_IS_EMERGENCY"),
    ({"emergency_action": "PANIC"}, "UNKNOWN_EMERGENCY_ACTION"),
    ({"emergency_action": None}, "MISSING_EMERGENCY_ACTION"),
    ({"slots": "Queens"}, "INVALID_SLOTS"),
    ({"slots": {"borough": 7}}, "INVALID_SLOT_TYPE"),
    ({"emergency_category": 3}, "INVALID_EMERGENCY_CATEGORY_TYPE"),
])
def test_r01_invalid_input_is_reported_with_structured_finding(router, override, code):
    override = dict(override)  # do not mutate the shared parameter
    result = router.assign(make_co1(**override))
    assert result.status.value == "INVALID"
    assert result.dispatch_eligible is False
    finding = next(f for f in result.findings if f.code == code)
    assert finding.rule == "R01" and finding.severity.value == "INVALID" and finding.message


def test_r01_boundary_confidences_are_valid(router):
    for c in (0, 0.0, 1, 1.0):
        assert router.assign(make_co1(confidence=c)).status.value == "ASSIGNED"


def test_r01_request_type_normalisation_is_recorded(router):
    result = router.assign(make_co1(" rt022 "))
    assert result.request_type == "RT022" and result.status.value == "ASSIGNED"
    assert "REQUEST_TYPE_NORMALISED" in codes(result)


def test_r01_normalisation_can_be_disabled():
    strict = AgencySkillRouter.from_catalog(CATALOG_PATH, config=RouterConfig(normalize_request_type=False))
    assert strict.assign(make_co1("rt022")).status.value == "INVALID"


def test_r01_extra_fields_are_ignored_not_rejected(router):
    msg = make_co1(emergency_category="NONE", future_field={"x": 1})
    msg["slots"]["hazard_terms"] = ["flooding"]
    assert router.assign(msg).status.value == "ASSIGNED"


# ---------------------------------------------------------------- R02
def test_r02_continue_proceeds(router):
    assert router.assign(make_co1()).dispatch_eligible is True


def test_r02_stop_call_911_blocks(router):
    r = router.assign(make_co1(is_emergency=True, emergency_action="STOP_CALL_911"))
    assert r.status.value == "BLOCKED" and r.next_step.value == "CITIZEN_TOLD_TO_CALL_911"
    assert "EMERGENCY_STOP_CALL_911" in codes(r)


def test_r02_confirm_with_citizen_holds(router):
    r = router.assign(make_co1(emergency_action="CONFIRM_WITH_CITIZEN"))
    assert r.status.value == "HELD" and r.next_step.value == "CONFIRM_WITH_CITIZEN"


def test_r02_emergency_flag_with_continue_is_a_contradiction(router):
    r = router.assign(make_co1(is_emergency=True, emergency_action="CONTINUE"))
    assert r.status.value == "HELD" and "EMERGENCY_CONTRADICTION" in codes(r)


def test_r02_stop_without_flag_still_blocks_and_warns(router):
    r = router.assign(make_co1(is_emergency=False, emergency_action="STOP_CALL_911"))
    assert r.status.value == "BLOCKED" and "EMERGENCY_FIELDS_INCONSISTENT" in codes(r)


# ---------------------------------------------------------------- R03 / R04
def test_r03_not_ready_holds(router):
    r = router.assign(make_co1(ready_to_submit=False))
    assert r.status.value == "HELD" and "NOT_READY_TO_SUBMIT" in codes(r)


def test_r04_needs_human_holds(router):
    r = router.assign(make_co1(needs_human=True))
    assert r.status.value == "HELD" and r.next_step.value == "HUMAN_REVIEW"


# ---------------------------------------------------------------- R05
@pytest.mark.parametrize("label, status, step", [
    ("EMERGENCY", "BLOCKED", "CITIZEN_TOLD_TO_CALL_911"),
    ("OTHER", "HELD", "ROUTE_TO_PERSON"),
    ("SMALLTALK", "HELD", "NO_REQUEST_CREATED"),
    ("smalltalk", "HELD", "NO_REQUEST_CREATED"),
])
def test_r05_special_labels_follow_intake_workflow(router, label, status, step):
    r = router.assign(make_co1(label))
    assert (r.status.value, r.next_step.value) == (status, step)
    assert r.agency is None and r.required_skills == []
    assert r.agency_resolution.value == "SPECIAL_LABEL"
    assert "R06" not in r.rules_applied  # no catalog lookup for labels


def test_r05_emergency_label_with_continue_is_flagged(router):
    r = router.assign(make_co1("EMERGENCY", emergency_action="CONTINUE", is_emergency=False))
    assert r.status.value == "BLOCKED" and "EMERGENCY_LABEL_CONTRADICTION" in codes(r)


# ---------------------------------------------------------------- R06 / R07 / R08 / R10 / R12
def test_r06_exact_lookup(router):
    r = router.assign(make_co1("RT022"))
    assert (r.agency, r.required_skills) == ("DEP", ["SEWER_DRAINAGE"])
    assert {"R06", "R10"} <= set(r.rules_applied)


def test_r07_unknown_code_is_unresolved_without_agency(router):
    r = router.assign(make_co1("RT999"))
    assert r.status.value == "UNRESOLVED" and r.agency is None and r.required_skills == []
    assert r.agency_resolution.value == "UNKNOWN_CODE" and "R12" in r.rules_applied


def test_r08_conflicting_catalog_entry_is_unresolved(make_router):
    rt = make_router([
        {"request_type_code": "RT001", "agency": "DOT", "required_skill": "ROAD_REPAIR", "dispatchable": "TRUE"},
        {"request_type_code": "RT001", "agency": "DEP", "required_skill": "ROAD_REPAIR", "dispatchable": "TRUE"},
    ])
    r = rt.assign(make_co1("RT001"))
    assert r.status.value == "UNRESOLVED" and r.agency is None
    assert r.next_step.value == "CATALOG_REVIEW" and "CATALOG_CONFLICT" in codes(r)


def test_r08_missing_agency_is_unresolved(make_router):
    rt = make_router([{"request_type_code": "RT001", "agency": "", "required_skill": "ROAD_REPAIR",
                       "dispatchable": "TRUE"}])
    r = rt.assign(make_co1("RT001"))
    assert r.status.value == "UNRESOLVED" and "CATALOG_MISSING_AGENCY" in codes(r)


def test_r10_missing_skill_never_leaks_partial_assignment(make_router):
    rt = make_router([{"request_type_code": "RT001", "agency": "DOT", "required_skill": "",
                       "dispatchable": "TRUE"}])
    r = rt.assign(make_co1("RT001"))
    assert r.status.value == "UNRESOLVED" and "CATALOG_MISSING_SKILLS" in codes(r)
    assert r.agency is None and r.required_skills == []  # R12: no agency without skills


def test_r10_multiple_skills_are_returned_in_order(make_router):
    rt = make_router([{"request_type_code": "RT001", "agency": "DOT",
                       "required_skill": "ROAD_REPAIR|SIDEWALK_REPAIR", "dispatchable": "TRUE"}])
    assert rt.assign(make_co1("RT001")).required_skills == ["ROAD_REPAIR", "SIDEWALK_REPAIR"]


def test_r13_non_dispatchable_catalog_entry_is_held(make_router):
    rt = make_router([{"request_type_code": "RT001", "agency": "DOT", "required_skill": "ROAD_REPAIR",
                       "dispatchable": "FALSE"}])
    r = rt.assign(make_co1("RT001"))
    assert r.status.value == "HELD" and r.agency == "DOT" and "CATALOG_NOT_DISPATCHABLE" in codes(r)


# ---------------------------------------------------------------- R09
def test_r09_confidence_is_preserved_not_reinterpreted(router):
    r = router.assign(make_co1(confidence=0.42))
    assert r.request.confidence == 0.42 and r.status.value == "ASSIGNED"  # no threshold by default
    assert r.to_dict()["confidence"] == 0.42  # passed through under the agreed name


def test_r09_configurable_threshold_holds_low_confidence():
    rt = AgencySkillRouter.from_catalog(CATALOG_PATH, config=RouterConfig(min_confidence=0.6))
    assert rt.assign(make_co1(confidence=0.59)).status.value == "HELD"
    assert rt.assign(make_co1(confidence=0.60)).status.value == "ASSIGNED"


def test_r09_rejects_invalid_threshold():
    with pytest.raises(ValueError):
        RouterConfig(min_confidence=1.5)


# ---------------------------------------------------------------- R11
def test_r11_explanation_names_every_blocking_rule(router):
    r = router.assign(make_co1("RT999", needs_human=True, ready_to_submit=False))
    for rule in ("R03", "R04", "R07"):
        assert f"[{rule}]" in r.explanation
    assert "R11" in r.rules_applied


def test_r11_assigned_explanation_mentions_agency_and_skill(router):
    r = router.assign(make_co1("RT004"))
    assert "DOT" in r.explanation and "STREET_LIGHTING" in r.explanation


# ---------------------------------------------------------------- R13 exhaustive gate check
@pytest.mark.parametrize("ready, human, emergency, action, rt", list(itertools.product(
    [True, False], [True, False], [True, False], ["CONTINUE", "CONFIRM_WITH_CITIZEN", "STOP_CALL_911"],
    ["RT022", "RT999", "OTHER", "EMERGENCY"])))
def test_r13_eligible_only_when_every_gate_passes(router, ready, human, emergency, action, rt):
    r = router.assign(make_co1(rt, ready_to_submit=ready, needs_human=human, is_emergency=emergency,
                               emergency_action=action))
    all_clear = ready and not human and not emergency and action == "CONTINUE" and rt == "RT022"
    assert r.dispatch_eligible is all_clear
    assert (r.status.value == "ASSIGNED") is all_clear
    assert r.needs_review is (not all_clear)


def test_r13_status_precedence(router):
    # INVALID beats BLOCKED beats HELD beats UNRESOLVED.
    assert router.assign(make_co1("RT999", confidence=5, emergency_action="STOP_CALL_911")).status.value == "INVALID"
    assert router.assign(make_co1("RT999", needs_human=True, emergency_action="STOP_CALL_911")).status.value == "BLOCKED"
    assert router.assign(make_co1("RT999", needs_human=True)).status.value == "HELD"


# ---------------------------------------------------------------- R14
def test_r14_elevated_advisory_is_informational_by_default(router):
    r = router.assign(make_co1("RT043"))
    assert r.status.value == "ASSIGNED" and r.agency == "DSNY"  # catalog unchanged
    assert r.referral_advisory.risk_level == "ELEVATED"
    assert r.referral_advisory.effect == "INFORMATIONAL"
    assert "NYPD" in r.referral_advisory.documented_alternative_agencies


def test_r14_review_policy_holds_elevated_codes_only():
    rt = AgencySkillRouter.from_catalog(CATALOG_PATH, config=RouterConfig(review_on_elevated_referral_risk=True))
    assert rt.assign(make_co1("RT043")).status.value == "HELD"
    assert rt.assign(make_co1("RT120", slots={"borough": "QUEENS"})).status.value == "ASSIGNED"


def test_r14_uses_borough_rate_when_available(router):
    with_b = router.assign(make_co1("RT043", slots={"borough": "QUEENS"})).referral_advisory
    without_b = router.assign(make_co1("RT043", slots={"borough": None})).referral_advisory
    assert with_b.basis == "RT+BOROUGH" and without_b.basis == "RT"


def test_r14_not_attached_to_blocked_requests(router):
    r = router.assign(make_co1("RT043", is_emergency=True, emergency_action="STOP_CALL_911"))
    assert r.referral_advisory is None


def test_r14_absent_without_knowledge(router_no_knowledge):
    r = router_no_knowledge.assign(make_co1("RT043"))
    assert r.referral_advisory is None and "R14" not in r.rules_applied
    assert router_no_knowledge.knowledge_status == "NOT_CONFIGURED"


def test_r14_stale_knowledge_is_disabled(write_catalog, tmp_path):
    cat = write_catalog([{"request_type_code": "RT043", "agency": "DSNY", "required_skill": "VEHICLE_REMOVAL",
                          "dispatchable": "TRUE"}])
    rt = AgencySkillRouter.from_catalog(cat, knowledge_path=KNOWLEDGE_PATH)
    assert rt.knowledge_status == "DISABLED_STALE_CATALOG"
    assert rt.assign(make_co1("RT043")).referral_advisory is None


def test_r14_broken_knowledge_file_does_not_stop_routing(tmp_path):
    bad = tmp_path / "k.json"
    bad.write_text(json.dumps({"artifact": "something else"}), encoding="utf-8")
    rt = AgencySkillRouter.from_catalog(CATALOG_PATH, knowledge_path=bad)
    assert rt.knowledge_status == "ERROR"
    assert rt.assign(make_co1("RT022")).status.value == "ASSIGNED"
