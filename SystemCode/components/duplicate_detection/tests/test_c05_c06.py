"""C05 duplicate and C06 fake-risk tests, in one file.

Contract, safety rules for every strategy, the hdbscan and BBL regressions, the labelling
tools, and real c06_credibility output feeding c06_fake.  Run: py -m pytest components/tests
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from components import c06_credibility as cred
from components.c05_duplicate import (
    STRATEGIES as C05_STRATEGIES, DuplicateCandidate, DuplicateRelation, DuplicateResult, RequestFacts,
    _hdbscan_labels, _single_linkage_labels, run as c05_run,
)
from components.c06_fake import (
    FIRST_TIME_LOWER_FLOOR, STRATEGIES as C06_STRATEGIES, CredibilitySnapshot, FakeAction, run as c06_run,
)
from tools.c05_labelling import (
    SET_LABEL_VALUES as LABEL_VALUES, attach_weak_labels, build_candidate_sheet, cohens_kappa,
    enumerate_candidate_pairs, load_requests, stratify_sample, write_kit,
)


# ============================================================================
# Builders
# ============================================================================

T0 = datetime(2026, 10, 3, 11, 42, tzinfo=timezone.utc)


_FACTS = dict(
    request_id="SR-00012345",
    created_at=T0,
    borough="BROOKLYN",
    request_type_code="RT021",
    complaint_type="Sewer",
    descriptor="Catch Basin Clogged/Flooding (Use Comments) (SC)",
    complaint_family="WATER_SEWER",
    location_type="Street",
    incident_address="1017 NOSTRAND AVENUE",
    incident_zip="11225",
    address_type="ADDRESS",
    latitude=40.6501,
    longitude=-73.9496,
    channel="VOICE_BOT",
    citizen_id="CIT-000042",
    free_text="The drain outside my house is flooding and water is reaching the sidewalk",
)


def facts(**over) -> RequestFacts:
    payload = dict(_FACTS)
    payload.update(over)
    return RequestFacts(**payload)


def later(minutes: int) -> datetime:
    return T0 + timedelta(minutes=minutes)


def sample_duplicate(**over) -> DuplicateResult:
    payload = dict(
        component="C05",
        version="0.1.0",
        relation=DuplicateRelation.NEW,
        duplicate_group_id="DG-00012345",
        is_primary=True,
        match_confidence=0.0,
        corroboration_count=1,
        distinct_channel_count=1,
    )
    payload.update(over)
    return DuplicateResult(**payload)


def sample_credibility(**over) -> CredibilitySnapshot:
    payload = dict(credibility_score=84.0, observation_count=6, credibility_band="TRUSTED")
    payload.update(over)
    return CredibilitySnapshot(**payload)


__all__ = [
    "facts", "later", "T0", "DuplicateCandidate",
    "sample_duplicate", "sample_credibility",
]


# ============================================================================
# Layout: beside Sankalp's four
# ============================================================================

def test_sankalp_credibility_still_present():
    from components import c06_credibility
    assert hasattr(c06_credibility, "assess")
    assert hasattr(c06_credibility, "CredibilityResult")


def test_nabeel_imports():
    from components.c05_duplicate import run as c05_run
    from components.c06_fake import run as c06_run
    assert callable(c05_run) and callable(c06_run)


def test_handle_message_untouched():
    from components import handle_message
    assert callable(handle_message)


# ============================================================================
# C05 acceptance
# ============================================================================

def cand(is_open=True, closed_at=None, **over):
    f = facts(**{"request_id": "SR-00000001", "citizen_id": "CIT-000077", "channel": "PHONE", **over})
    return DuplicateCandidate(facts=f, group_id="DG-00000001", is_open=is_open, closed_at=closed_at,
                              reporters=frozenset({"CIT-000077"}), channels=frozenset({"PHONE"}))


def test_same_incident_other_channel_is_linked():
    r = c05_run(facts(created_at=later(30), latitude=40.6502, longitude=-73.9495), [cand()])
    assert r.relation == DuplicateRelation.DUPLICATE and not r.is_primary and r.duplicate_of == "SR-00000001"
    assert r.corroboration_count == 2 and r.distinct_channel_count == 2 and r.match_evidence


def test_different_type_same_place_not_merged():
    r = c05_run(facts(created_at=later(30)), [cand(request_type_code="RT004")])
    assert r.is_primary and r.relation == DuplicateRelation.NEW


def test_after_closure_is_recurrence_not_duplicate():
    r = c05_run(facts(created_at=later(60 * 24 * 5)), [cand(is_open=False, closed_at=later(60))])
    assert r.relation == DuplicateRelation.RECURRENCE and r.is_primary and r.recurrence_of == "SR-00000001"


def test_far_away_not_merged():
    r = c05_run(facts(created_at=later(10), latitude=40.70, longitude=-73.90), [cand()])
    assert r.relation == DuplicateRelation.NEW


# ============================================================================
# C05 safety, every strategy
# ============================================================================

ALL_STRATEGIES = [s for s in C05_STRATEGIES if s != "hybrid_no_fuzzy"]


def test_every_strategy_rejects_different_type():
    for name in ALL_STRATEGIES:
        r = c05_run(facts(created_at=later(30)), [cand(request_type_code="RT004")], strategy=name)
        assert r.relation == DuplicateRelation.NEW, name
        assert r.is_primary, name


def test_every_strategy_treats_closure_as_recurrence_or_new():
    """A closed candidate must never become DUPLICATE."""
    for name in ALL_STRATEGIES:
        r = c05_run(facts(created_at=later(60 * 24 * 5)),
                    [cand(is_open=False, closed_at=later(60))], strategy=name)
        assert r.relation != DuplicateRelation.DUPLICATE, name
        if r.relation == DuplicateRelation.RECURRENCE:
            assert r.is_primary and r.recurrence_of == "SR-00000001"


def test_every_strategy_keeps_far_points_new():
    for name in ALL_STRATEGIES:
        r = c05_run(facts(created_at=later(10), latitude=40.70, longitude=-73.90),
                    [cand()], strategy=name)
        assert r.relation == DuplicateRelation.NEW, name


def test_empty_candidates_are_new():
    r = c05_run(facts(), [])
    assert r.relation == DuplicateRelation.NEW and r.is_primary


# ============================================================================
# C05 regressions: hdbscan, BBL text
# ============================================================================

def _many_candidates(n=40):
    rng = np.random.default_rng(0)
    out = []
    for i in range(n):
        f = facts(request_id=f"SR-{i:08d}", citizen_id=f"CIT-{i:06d}", channel="PHONE",
                  created_at=later(-int(rng.integers(1, 47 * 60))),
                  latitude=40.6501 + float(rng.uniform(-0.002, 0.002)),
                  longitude=-73.9495 + float(rng.uniform(-0.002, 0.002)))
        out.append(DuplicateCandidate(facts=f, group_id=f"DG-{i:08d}", is_open=bool(i % 2),
                                      closed_at=None, reporters=frozenset(), channels=frozenset({"PHONE"})))
    return out


def test_hdbscan_runs_on_40_candidates():
    r = c05_run(facts(created_at=later(30)), _many_candidates(40), strategy="hdbscan")
    assert r.relation in (DuplicateRelation.NEW, DuplicateRelation.DUPLICATE, DuplicateRelation.RECURRENCE)


def test_hdbscan_labels_never_raise_and_match_fallback_shape():
    rng = np.random.default_rng(0)
    X = np.vstack([[0, 0, 0], rng.uniform(-300, 300, (40, 3))])
    labels = _hdbscan_labels(X)
    assert labels.shape == (41,)
    sl = _single_linkage_labels(np.array([[0, 0, 0], [10, 0, 0], [900, 0, 0]], float), 160.0)
    assert sl[0] == sl[1] and sl[0] >= 0 and sl[2] == -1


def test_bbl_float_text_matches_integer_text():
    from components.c05_duplicate import usable_bbl
    assert usable_bbl("2048330028.0") == usable_bbl("2048330028") == "2048330028"
    assert usable_bbl("0.0") is None


# ============================================================================
# C05 labelling kit
# ============================================================================

def test_sampler_stratifies_and_has_no_model_score(tmp_path: Path):
    df, origin = load_requests("sample")
    pool = enumerate_candidate_pairs(df)
    assert origin == "sample"
    if pool.empty:
        return
    sampled = stratify_sample(pool, n=min(40, len(pool)), seed=2026)
    assert "model_score" not in sampled.columns
    assert "unfounded_probability" not in sampled.columns
    assert "resolution_description" not in sampled.columns
    assert sampled["pair_id"].nunique() == len(sampled)
    assert set(sampled["distance_bin"]).issubset({"very_close", "medium", "far"})
    assigned = sampled.copy()
    assigned["labeller_id"] = "L1"
    assigned["assignment_set"] = "SOLO"
    paths = write_kit(sampled, assigned, tmp_path)
    sheet = pd.read_csv(paths["pairs"])
    for col in ("request_id_a", "request_id_b", "distance_m", "time_gap_hours",
                "address_a", "address_b", "channel_a", "open_closed_a", "label", "labeller_id"):
        assert col in sheet.columns
    assert "model_score" not in sheet.columns


def test_weak_labels_are_marked_provisional():
    df, _ = load_requests("sample")
    pool = enumerate_candidate_pairs(df)
    if pool.empty:
        return
    sampled = stratify_sample(pool, n=min(20, len(pool)), seed=2026)
    weak = attach_weak_labels(sampled)
    assert set(weak["label_source"]) == {"weak_heuristic"}
    assert set(weak["label"]).issubset({"DUPLICATE", "NEW", "RECURRENCE", "UNSURE"})


def test_kappa_perfect_and_disagreement():
    assert cohens_kappa(["DUPLICATE", "NEW"], ["DUPLICATE", "NEW"]) == 1.0
    k = cohens_kappa(["DUPLICATE", "NEW", "NEW"], ["NEW", "NEW", "DUPLICATE"])
    assert k < 1.0


# ============================================================================
# C05 candidate-set sheet
# ============================================================================

def test_sheet_has_candidates_a_and_blank_labels():
    pairs = pd.DataFrame([{"pair_id": "P1", "request_type_code": "RT120", "request_id_a": "A", "request_id_b": "B",
                           "created_at_a": "2026-01-01T10:00", "created_at_b": "2026-01-01T12:00",
                           "closed_at_a": None, "latitude_b": 40.65, "longitude_b": -73.95, "bbl_b": "3011110001.0",
                           "address_b": "1 MAIN STREET", "address_a": "1 MAIN STREET", "bbl_a": "3011110001",
                           "free_text_a": "x", "free_text_b": "y", "distance_m": 0.0, "label": "DUPLICATE"}])
    req = pd.DataFrame([
        {"request_id": "A", "request_type_code": "RT120", "created_at": "2026-01-01T10:00", "closed_date": None,
         "latitude": 40.65, "longitude": -73.95, "bbl": "3011110001", "incident_address": "1 MAIN STREET", "free_text": "x"},
        {"request_id": "C", "request_type_code": "RT120", "created_at": "2026-01-01T11:00", "closed_date": "2026-01-01T11:30",
         "latitude": 40.6501, "longitude": -73.95, "bbl": "3011110002", "incident_address": "3 MAIN STREET", "free_text": "z"},
        {"request_id": "FAR", "request_type_code": "RT120", "created_at": "2026-01-01T11:00", "closed_date": None,
         "latitude": 40.70, "longitude": -73.90, "bbl": None, "incident_address": "9 FAR AVE", "free_text": "z"},
        {"request_id": "OTHER_TYPE", "request_type_code": "RT004", "created_at": "2026-01-01T11:00", "closed_date": None,
         "latitude": 40.65, "longitude": -73.95, "bbl": "3011110001", "incident_address": "1 MAIN STREET", "free_text": "z"},
    ])
    s = build_candidate_sheet(pairs, req)
    assert set(s.candidate_request_id) == {"A", "C"}
    assert s.loc[s.candidate_request_id == "A", "is_labelled_a"].item()
    assert s.loc[s.candidate_request_id == "A", "same_bbl"].item()
    assert not s.loc[s.candidate_request_id == "C", "candidate_open_at_b"].item()
    assert (s.label_same_incident == "").all() and "SAME" in LABEL_VALUES


# ============================================================================
# C06 acceptance
# ============================================================================

def test_returns_calibrated_probability_and_reasons():
    r = c06_run(facts(), sample_duplicate(), sample_credibility())
    assert r.unfounded_probability is not None and 0 <= r.unfounded_probability <= 1
    assert r.risk_band is not None and r.contributing_factors


def test_never_rejects():
    bad = sample_credibility(credibility_score=5, observation_count=20)
    r = c06_run(facts(), sample_duplicate(), bad, 9)
    assert r.recommended_action in (FakeAction.PROCEED, FakeAction.LOWER_PRIORITY, FakeAction.HOLD_FOR_REVIEW)


def test_corroboration_lowers_risk():
    alone = c06_run(facts(), sample_duplicate(), sample_credibility())
    many = c06_run(facts(), replace(sample_duplicate(), corroboration_count=4, distinct_channel_count=3),
                   sample_credibility())
    assert many.unfounded_probability < alone.unfounded_probability


def test_burst_flagged():
    assert c06_run(facts(), sample_duplicate(), sample_credibility(),
                   recent_reports_by_citizen_1h=5).reporter_burst_flag


# ============================================================================
# C06 safety, every strategy
# ============================================================================

def test_every_strategy_never_rejects():
    bad = sample_credibility(credibility_score=5, observation_count=20)
    for name in C06_STRATEGIES:
        r = c06_run(facts(), sample_duplicate(), bad, 9, strategy=name)
        assert r.recommended_action in (
            FakeAction.PROCEED, FakeAction.LOWER_PRIORITY, FakeAction.HOLD_FOR_REVIEW,
        ), name
        assert r.unfounded_probability is None or 0 <= r.unfounded_probability <= 1


def test_every_strategy_corroboration_lowers_risk():
    many = replace(sample_duplicate(), corroboration_count=4, distinct_channel_count=3)
    for name in C06_STRATEGIES:
        alone = c06_run(facts(), sample_duplicate(), sample_credibility(), strategy=name)
        extra = c06_run(facts(), many, sample_credibility(), strategy=name)
        assert extra.unfounded_probability < alone.unfounded_probability, name


def test_every_strategy_flags_burst_at_five():
    for name in C06_STRATEGIES:
        r = c06_run(facts(), sample_duplicate(), sample_credibility(),
                    recent_reports_by_citizen_1h=5, strategy=name)
        assert r.reporter_burst_flag is True, name


# ============================================================================
# C06 with real credibility
# ============================================================================

def _hist(outcome, n):
    return [cred.Observation(outcome=outcome, closed_at=T0 - timedelta(days=i + 1), request_type="RT001")
            for i in range(n)]


def test_new_citizen_from_real_credibility_is_cold_start():
    c = cred.assess("CIT-000001", history=[], now=T0)
    assert c.observation_count == 0 and c.credibility_band == "NEW"
    r = c06_run(facts(), sample_duplicate(), c, 0)
    assert r.unfounded_probability is not None
    if r.unfounded_probability < FIRST_TIME_LOWER_FLOOR:
        assert r.recommended_action == FakeAction.PROCEED


def test_history_from_real_credibility_moves_probability_the_right_way():
    good = cred.assess("CIT-000002", history=_hist("GENUINE", 8), now=T0)
    bad = cred.assess("CIT-000003", history=_hist("UNFOUNDED", 8), now=T0)
    assert good.credibility_score > bad.credibility_score and bad.observation_count == 8
    pg = c06_run(facts(), sample_duplicate(), good, 0, strategy="rules").unfounded_probability
    pb = c06_run(facts(), sample_duplicate(), bad, 0, strategy="rules").unfounded_probability
    assert pb > pg
    for c in (good, bad):
        r = c06_run(facts(), sample_duplicate(), c, 0)
        assert r.recommended_action in (FakeAction.PROCEED, FakeAction.LOWER_PRIORITY, FakeAction.HOLD_FOR_REVIEW)


def test_anonymous_real_credibility_is_safe():
    c = cred.assess(None, now=T0)
    r = c06_run(facts(), None, c, 0)
    assert r.recommended_action != "REJECT" and r.unfounded_probability is not None
