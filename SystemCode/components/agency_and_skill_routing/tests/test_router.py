"""Public router API: exhaustive catalog coverage, output schema, serialisation, robustness."""

from __future__ import annotations

import json

import pytest

from civicops_agency import AgencySkillRouter, AssignmentResult, Status
from civicops_agency.simulator import make_co1

from conftest import CATALOG_CODES, CATALOG_PATH, CATALOG_ROWS

# The agreed CO1 fields, passed through, then the six fields this component adds.
CO1_KEYS = ["request_type", "confidence", "ready_to_submit", "needs_human", "is_emergency", "emergency_action",
            "emergency_category", "slots"]
ADDED_KEYS = ["agency", "required_skills", "status", "dispatch_eligible", "next_step", "explanation"]
EXPECTED_KEYS = CO1_KEYS + ADDED_KEYS
DETAIL_KEYS = ["schema_version", "component_version", "catalog_version", "decision_method", "agency_resolution",
               "agency_name", "citizen_label", "needs_review", "rules_applied", "findings", "referral_advisory"]
# Information owned by other components that must never appear in this output.
FORBIDDEN_KEYS = {"est_service_minutes", "service_minutes", "estimated_service_time", "default_urgency", "urgency",
                  "priority", "priority_rank", "crew", "crew_id", "travel_time", "route", "routes", "latitude",
                  "longitude", "coordinates", "external_factors", "resolution_time"}


def all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_keys(v)


# ---------------------------------------------------------- exhaustive coverage
@pytest.mark.parametrize("row", CATALOG_ROWS, ids=CATALOG_CODES)
def test_every_catalog_code_resolves_to_its_catalog_agency_and_skill(router, row):
    r = router.assign(make_co1(row["request_type_code"]))
    assert r.status == Status.ASSIGNED
    assert r.agency == row["agency"]
    assert r.required_skills == row["required_skill"].split("|")
    assert r.citizen_label == row["citizen_label"]
    assert r.dispatch_eligible is True and r.needs_review is False
    assert r.decision_method == "CATALOG_RULE_BASED"


@pytest.mark.parametrize("code", CATALOG_CODES)
def test_every_catalog_code_is_blocked_by_911(router, code):
    r = router.assign(make_co1(code, is_emergency=True, emergency_action="STOP_CALL_911"))
    assert r.status == Status.BLOCKED and r.dispatch_eligible is False


def test_agency_is_identical_with_and_without_knowledge(router, router_no_knowledge):
    for code in CATALOG_CODES:
        a, b = router.assign(make_co1(code)), router_no_knowledge.assign(make_co1(code))
        assert (a.agency, a.required_skills, a.status) == (b.agency, b.required_skills, b.status)


# ---------------------------------------------------------- output schema
@pytest.mark.parametrize("msg", [make_co1(), make_co1("RT999"), make_co1("OTHER"), make_co1(confidence=9), "junk"])
def test_slim_output_has_exactly_the_agreed_keys(router, msg):
    d = router.assign(msg).to_dict()
    assert list(d) == EXPECTED_KEYS
    assert list(d["slots"]) == ["incident_address", "cross_street", "borough", "landmark"]


def test_detail_mode_adds_one_audit_block(router):
    d = router.assign(make_co1("RT043")).to_dict(detail=True)
    assert list(d) == EXPECTED_KEYS + ["detail"]
    assert list(d["detail"]) == DETAIL_KEYS
    assert d["detail"]["referral_advisory"]["risk_level"] == "ELEVATED"


def test_co1_fields_are_passed_through_unchanged(router):
    msg = make_co1("RT004", confidence=0.71, emergency_category="NONE",
                   slots={"incident_address": "1 MAIN ST", "cross_street": "ELM", "borough": "BRONX",
                          "landmark": "PARK"})
    d = router.assign(msg).to_dict()
    for key in CO1_KEYS:
        assert d[key] == msg[key], key


def test_fields_outside_the_contract_are_not_forwarded(router):
    d = router.assign(make_co1(request_id="SR-1", hazard_terms=["flood"])).to_dict(detail=True)
    assert "request_id" not in d and "hazard_terms" not in d
    assert d["status"] == "ASSIGNED"


def test_invalid_field_is_not_forwarded(router):
    d = router.assign(make_co1(confidence=1.7)).to_dict()
    assert d["confidence"] is None and d["status"] == "INVALID" and "1.7" in d["explanation"]


def test_next_component_is_duplicate_detection(router):
    assert router.assign(make_co1()).to_dict()["next_step"] == "DUPLICATE_DETECTION"


@pytest.mark.parametrize("msg", [make_co1(), make_co1("RT999"), make_co1("OTHER"), make_co1(confidence=9),
                                 make_co1(emergency_action="STOP_CALL_911", is_emergency=True)])
def test_output_never_contains_other_components_fields(router, msg):
    assert not FORBIDDEN_KEYS & set(all_keys(router.assign(msg).to_dict(detail=True)))


def test_json_round_trip(router):
    r = router.assign(make_co1("RT043"))
    assert json.loads(r.to_json()) == r.to_dict()
    assert json.loads(r.to_json(detail=True)) == r.to_dict(detail=True)


def test_location_is_preserved_and_borough_normalised(router):
    r = router.assign(make_co1(slots={"incident_address": " 1 MAIN ST ", "borough": "staten  island",
                                      "landmark": "", "cross_street": None}))
    assert r.location.to_dict() == {"incident_address": "1 MAIN ST", "cross_street": None,
                                    "borough": "STATEN ISLAND", "landmark": None}


def test_flat_dotted_slot_keys_are_accepted(router):
    msg = make_co1(drop=["slots"])
    msg["slots.incident_address"] = "9 ELM ST"
    msg["slots.borough"] = "BRONX"
    r = router.assign(msg)
    assert r.location.borough == "BRONX" and r.location.incident_address == "9 ELM ST"


def test_unknown_borough_warns_but_does_not_block(router):
    r = router.assign(make_co1(slots={"borough": "NEWARK"}))
    assert r.status == Status.ASSIGNED and r.location.borough == "NEWARK"
    assert any(f.code == "UNKNOWN_BOROUGH" and f.severity.value == "WARNING" for f in r.findings)


def test_missing_location_warns_but_does_not_block(router):
    r = router.assign(make_co1(drop=["slots"]))
    assert r.status == Status.ASSIGNED and r.location.is_empty()
    assert any(f.code == "LOCATION_MISSING" for f in r.findings)


def test_emergency_fields_are_passed_through(router):
    d = router.assign(make_co1(is_emergency=True, emergency_action="STOP_CALL_911",
                               emergency_category="FIRE")).to_dict()
    assert (d["is_emergency"], d["emergency_action"], d["emergency_category"]) == (True, "STOP_CALL_911", "FIRE")


# ---------------------------------------------------------- input forms
def test_json_string_and_bytes_input(router):
    text = json.dumps(make_co1("RT004"))
    assert router.assign(text).agency == "DOT"
    assert router.assign(text.encode("utf-8")).agency == "DOT"


def test_invalid_json_text_is_structured_invalid(router):
    r = router.assign("{not json")
    assert r.status == Status.INVALID and r.findings[0].code == "INVALID_JSON"


def test_assign_json_object_and_array(router):
    single = json.loads(router.assign_json(json.dumps(make_co1())))
    many = json.loads(router.assign_json(json.dumps([make_co1(), make_co1("RT999")])))
    assert single["status"] == "ASSIGNED"
    assert [m["status"] for m in many] == ["ASSIGNED", "UNRESOLVED"]


def test_batch_items_are_independent(router):
    results = router.assign_batch([make_co1("RT001"), None, "garbage", make_co1("RT141"), 42])
    assert [r.status.value for r in results] == ["ASSIGNED", "INVALID", "INVALID", "ASSIGNED", "INVALID"]
    assert all(isinstance(r, AssignmentResult) for r in results)


@pytest.mark.parametrize("junk", [None, 0, 1.5, "", "[]", [], {}, {"request_type": None}, {"slots": []},
                                  {"request_type": ["RT001"]}, b"\xff\xfe", {"confidence": float("inf")}])
def test_router_never_raises_on_junk(router, junk):
    r = router.assign(junk)
    assert r.status == Status.INVALID and r.dispatch_eligible is False


def test_default_constructor_finds_bundled_catalog():
    assert AgencySkillRouter.default().catalog.version == AgencySkillRouter.from_catalog(CATALOG_PATH).catalog.version


def test_router_describe_reports_configuration(router):
    d = router.describe()
    assert d["catalog_codes"] == 34 and d["referral_knowledge"] == "ACTIVE"
