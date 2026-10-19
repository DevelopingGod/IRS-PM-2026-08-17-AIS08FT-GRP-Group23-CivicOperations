"""End-to-end: simulated CO1 traffic, downstream dispatch contract, and history replay."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from civicops_agency import Status
from civicops_agency.simulator import from_history_row, random_stream, scenario_suite

from conftest import ROOT, SHARED_AGENCIES, SHARED_SKILLS

PARQUET = ROOT / "data" / "research" / "requests_real.parquet"


def test_simulator_scenario_suite_matches_expectations(router):
    failures = []
    for s in scenario_suite(router.catalog):
        r = router.assign(s["input"])
        if r.status.value != s["expected_status"] or r.dispatch_eligible != s["expected_eligible"]:
            failures.append((s["name"], r.status.value, r.dispatch_eligible))
        if s["expected_rule"]:
            assert s["expected_rule"] in r.rules_applied, s["name"]
    assert failures == []


def test_suite_covers_every_catalog_code(router):
    names = {s["name"] for s in scenario_suite(router.catalog)}
    assert {f"valid_{c}" for c in router.catalog.codes()} <= names


def test_random_stream_invariants(router):
    for r in router.assign_batch(random_stream(router.catalog, 3000, seed=11)):
        d = r.to_dict(detail=True)
        assert d["dispatch_eligible"] == (d["status"] == "ASSIGNED")
        assert d["detail"]["needs_review"] == (d["status"] != "ASSIGNED")
        if d["dispatch_eligible"]:
            assert d["agency"] and d["required_skills"] and d["next_step"] == "DUPLICATE_DETECTION"
        if d["status"] in ("UNRESOLVED",) or d["detail"]["agency_resolution"] != "RESOLVED":
            assert d["agency"] is None and d["required_skills"] == []
        assert d["explanation"] and d["detail"]["rules_applied"]


class MockDispatch:
    """Stand-in for the downstream pipeline (duplicate detection onward to crew deployment).

    It reads only the slim hand-off fields and applies the shared vocabulary
    (8 agencies, 16 skills) that the crew table uses. It is the consumer-side
    contract test for this component's output.
    """

    def __init__(self):
        self.queue = []

    def accept(self, payload: dict) -> bool:
        if not payload["dispatch_eligible"]:
            return False
        assert payload["agency"] in SHARED_AGENCIES
        assert set(payload["required_skills"]) <= SHARED_SKILLS
        assert payload["next_step"] == "DUPLICATE_DETECTION"
        assert payload["slots"] is not None
        self.queue.append({"request_type": payload["request_type"], "agency": payload["agency"],
                           "skills": payload["required_skills"], "borough": payload["slots"]["borough"]})
        return True


def test_downstream_dispatch_can_consume_json_output(router):
    dispatch = MockDispatch()
    stream = random_stream(router.catalog, 500, seed=3)
    wire = json.loads(router.assign_json(json.dumps(stream)))  # through JSON, like a queue
    accepted = [dispatch.accept(p) for p in wire]
    assert sum(accepted) == len(dispatch.queue) > 0
    assert all(not p["dispatch_eligible"] for p, ok in zip(wire, accepted) if not ok)


@pytest.mark.skipif(not PARQUET.exists(), reason="historical dataset is a research-only file")
def test_history_replay_agrees_with_first_agency_on_test_split(router):
    pd = pytest.importorskip("pandas")
    df = pd.read_parquet(PARQUET, columns=["unique_key", "request_type_code", "borough", "agency", "split"])
    test = df[df["split"] == "test"]
    results = router.assign_batch(from_history_row(r) for r in test.to_dict("records"))
    assert all(r.status == Status.ASSIGNED for r in results)
    agree = sum(r.agency == a for r, a in zip(results, test["agency"]))
    assert agree == len(test)  # test split: no catalog/historical disagreement (see research outputs)


AGREED_CO1_FIELDS = {"request_type", "confidence", "ready_to_submit", "needs_human", "is_emergency",
                     "emergency_action", "emergency_category", "slots"}


def test_example_inputs_contain_only_agreed_co1_fields():
    for name in ("valid_co1.json", "held_co1.json", "emergency_co1.json", "unknown_code.json"):
        assert (ROOT / "examples" / "inputs" / name).is_file()
    for path in (ROOT / "examples" / "inputs").glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        for item in (data if isinstance(data, list) else [data]):
            assert set(item) <= AGREED_CO1_FIELDS, path.name  # no agency, skills or status in inputs


def test_example_outputs_are_in_sync_with_the_code(router):
    sys.path.insert(0, str(ROOT / "examples"))
    from generate_outputs import render

    for path in (ROOT / "examples" / "inputs").glob("*.json"):
        expected = (ROOT / "examples" / "outputs" / path.name).read_text(encoding="utf-8")
        assert render(router, path) == expected, f"regenerate: python examples/generate_outputs.py ({path.name})"
