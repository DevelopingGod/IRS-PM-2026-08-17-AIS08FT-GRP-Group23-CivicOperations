"""Shared fixtures. Tests run from a source checkout or extracted ZIP without installation."""

from __future__ import annotations

import csv
import sys
from pathlib import Path
from typing import Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_agency import AgencySkillRouter, RouterConfig, load_catalog  # noqa: E402

CATALOG_PATH = ROOT / "data" / "reference" / "request_catalog.csv"
MATRIX_PATH = ROOT / "data" / "reference" / "NYC311_agency_matrix.md"
KNOWLEDGE_PATH = ROOT / "data" / "reference" / "referral_knowledge.json"

# Allowed values agreed in the shared data reference (page 3). Used to check
# that what this component emits is consumable by crew deployment.
SHARED_AGENCIES = {"DEP", "DOB", "DOHMH", "DOT", "DPR", "DSNY", "HPD", "NYPD"}
SHARED_SKILLS = {
    "BUILDING_INSPECTION", "FORESTRY", "HOUSING_INSPECTION", "NOISE_PATROL", "PARKING_ENFORCEMENT",
    "PEST_CONTROL", "ROAD_REPAIR", "SANITATION_COLLECTION", "SEWER_DRAINAGE", "SIDEWALK_REPAIR", "SNOW_ICE",
    "STREET_CLEANING", "STREET_LIGHTING", "TRAFFIC_SIGNALS", "VEHICLE_REMOVAL", "WATER_MAINS",
}


def read_catalog_rows() -> List[Dict[str, str]]:
    """Independent reading of the CSV (plain csv module) to cross-check the loader."""
    with CATALOG_PATH.open(encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


CATALOG_ROWS = read_catalog_rows()
CATALOG_CODES = [r["request_type_code"] for r in CATALOG_ROWS]


@pytest.fixture(scope="session")
def router() -> AgencySkillRouter:
    return AgencySkillRouter.from_catalog(CATALOG_PATH)


@pytest.fixture(scope="session")
def router_no_knowledge() -> AgencySkillRouter:
    return AgencySkillRouter.from_catalog(CATALOG_PATH, knowledge_path=None)


@pytest.fixture(scope="session")
def catalog():
    return load_catalog(CATALOG_PATH)


@pytest.fixture
def write_catalog(tmp_path):
    """Write a custom catalog CSV and return its path (for integrity tests)."""

    def _write(rows: List[Dict[str, str]], name: str = "request_catalog.csv",
               header: List[str] = None) -> Path:
        path = tmp_path / name
        fields = header or ["request_type_code", "citizen_label", "agency", "required_skill", "dispatchable"]
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        return path

    return _write


@pytest.fixture
def make_router(write_catalog):
    def _make(rows, **config) -> AgencySkillRouter:
        return AgencySkillRouter.from_catalog(write_catalog(rows), config=RouterConfig(**config))

    return _make
