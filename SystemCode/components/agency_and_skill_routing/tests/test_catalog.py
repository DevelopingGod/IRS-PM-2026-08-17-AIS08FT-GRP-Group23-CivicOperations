"""Catalog loading and integrity (rule R08) - real catalog and synthetic defects."""

from __future__ import annotations

import re

import pytest

from civicops_agency import CatalogError, load_agency_names, load_catalog
from civicops_agency.catalog import catalog_from_rows, content_hash

from conftest import CATALOG_PATH, CATALOG_ROWS, MATRIX_PATH, SHARED_AGENCIES, SHARED_SKILLS

ROW = {"request_type_code": "RT001", "citizen_label": "Pothole", "agency": "DOT", "required_skill": "ROAD_REPAIR",
       "dispatchable": "TRUE"}


def row(**kw):
    r = dict(ROW)
    r.update(kw)
    return r


# ---------------------------------------------------------------- real catalog
def test_real_catalog_loads_every_code_without_issues(catalog):
    assert len(catalog.entries) == len(CATALOG_ROWS) == 34
    assert catalog.issues == []
    assert all(e.ok for e in catalog.entries.values())
    assert catalog.integrity_report()["usable"] is True


def test_real_catalog_codes_are_unique_and_well_formed(catalog):
    codes = [r["request_type_code"] for r in CATALOG_ROWS]
    assert len(codes) == len(set(codes))
    assert all(re.fullmatch(r"RT\d{3}", c) for c in codes)


def test_real_catalog_uses_exactly_the_shared_agencies_and_skills(catalog):
    report = catalog.integrity_report()
    assert set(report["agencies"]) == SHARED_AGENCIES
    assert set(report["skills"]) == SHARED_SKILLS


def test_loader_matches_independent_csv_reading(catalog):
    for r in CATALOG_ROWS:
        e = catalog.lookup(r["request_type_code"])
        assert e.agency == r["agency"]
        assert list(e.required_skills) == r["required_skill"].split("|")


def test_catalog_hash_ignores_line_endings_and_bom():
    text = CATALOG_PATH.read_text(encoding="utf-8")
    assert content_hash(text) == content_hash(text.replace("\n", "\r\n")) == content_hash("﻿" + text)


def test_service_minutes_and_urgency_are_not_loaded(catalog):
    entry = catalog.lookup("RT022")
    assert not hasattr(entry, "est_service_minutes")
    assert not hasattr(entry, "default_urgency")


# ------------------------------------------------------------ synthetic defects
def test_identical_duplicates_collapse_with_warning():
    cat = catalog_from_rows([row(), row()])
    assert cat.lookup("RT001").ok
    assert [i.code for i in cat.issues] == ["DUPLICATE_IDENTICAL"]


def test_conflicting_duplicates_are_not_resolved():
    cat = catalog_from_rows([row(), row(agency="DEP")])
    entry = cat.lookup("RT001")
    assert entry.state == "CONFLICT" and entry.agency is None
    assert set(entry.candidate_agencies) == {"DOT", "DEP"}
    assert any(i.code == "CONFLICT" for i in cat.issues)


def test_conflicting_skills_are_not_resolved():
    entry = catalog_from_rows([row(), row(required_skill="SIDEWALK_REPAIR")]).lookup("RT001")
    assert entry.state == "CONFLICT"


def test_missing_agency_marks_entry_incomplete():
    cat = catalog_from_rows([row(agency="")])
    assert cat.lookup("RT001").state == "INCOMPLETE"
    assert any(i.code == "MISSING_AGENCY" for i in cat.issues)


def test_missing_skill_marks_entry_incomplete():
    cat = catalog_from_rows([row(required_skill="  ")])
    assert cat.lookup("RT001").state == "INCOMPLETE"
    assert any(i.code == "MISSING_SKILL" for i in cat.issues)


def test_malformed_code_row_is_skipped():
    cat = catalog_from_rows([row(), row(request_type_code="R-7")])
    assert cat.codes() == ["RT001"]
    assert any(i.code == "MALFORMED_CODE" for i in cat.issues)


def test_multiple_skills_use_pipe_separator():
    entry = catalog_from_rows([row(required_skill="ROAD_REPAIR| SIDEWALK_REPAIR |ROAD_REPAIR")]).lookup("RT001")
    assert entry.required_skills == ("ROAD_REPAIR", "SIDEWALK_REPAIR")


def test_dispatchable_parsing_is_fail_safe():
    cat = catalog_from_rows([row(), row(request_type_code="RT002", dispatchable="FALSE"),
                             row(request_type_code="RT003", dispatchable="maybe")])
    assert cat.lookup("RT001").dispatchable is True
    assert cat.lookup("RT002").dispatchable is False
    assert cat.lookup("RT003").dispatchable is None


def test_missing_required_column_raises(write_catalog):
    path = write_catalog([{"request_type_code": "RT001", "agency": "DOT"}],
                         header=["request_type_code", "agency"])
    with pytest.raises(CatalogError, match="required_skill"):
        load_catalog(path)


def test_missing_file_raises(tmp_path):
    with pytest.raises(CatalogError):
        load_catalog(tmp_path / "nope.csv")


def test_strict_mode_refuses_conflicts():
    with pytest.raises(CatalogError, match="CONFLICT"):
        catalog_from_rows([row(), row(agency="DEP")], strict=True)


def test_agency_names_come_from_matrix_glossary():
    names = load_agency_names(MATRIX_PATH)
    assert names["DEP"] == "New York City Department of Environmental Protection"
    assert SHARED_AGENCIES <= set(names)
