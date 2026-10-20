"""Paths, constants and reference loaders shared by every pipeline step."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DATA = REPO / "data"
REFERENCE = DATA / "reference"
RAW = DATA / "raw"            # git-ignored, local only
PROCESSED = DATA / "processed"  # git-ignored, local only (regenerate with the pipeline)
SAMPLE = DATA / "sample"      # small, committed: lets anyone run components without downloading

SEED = 2026
ROWS_PER_BOROUGH = 10_000
SAMPLE_MONTHS = 12
AGGREGATE_START = "2020-01-01"
SCHEMA_VERSION = "1.0.0"
SOCRATA = "https://data.cityofnewyork.us/resource/erm2-nwe9.json"

COLUMNS = [
    "unique_key", "created_date", "closed_date", "due_date", "agency", "agency_name",
    "complaint_type", "descriptor", "descriptor_2", "location_type", "incident_zip",
    "incident_address", "address_type", "bbl", "city", "status", "resolution_description",
    "resolution_action_updated_date", "community_board", "borough", "open_data_channel_type",
    "latitude", "longitude",
]

AGENCY_NAMES = {
    "DOT": "Department of Transportation", "DEP": "Department of Environmental Protection",
    "DSNY": "Department of Sanitation", "DPR": "Department of Parks and Recreation",
    "HPD": "Housing Preservation and Development", "DOHMH": "Department of Health and Mental Hygiene",
    "NYPD": "New York City Police Department (non-emergency)", "DOB": "Department of Buildings",
}


@dataclass(frozen=True)
class CatalogEntry:
    code: str
    label: str
    family: str
    complaint_types: tuple[str, ...]
    descriptor_regex: re.Pattern | None
    agency: str
    skill: str
    default_urgency: str
    service_minutes: int


def load_boroughs() -> pd.DataFrame:
    return pd.read_csv(REFERENCE / "boroughs.csv")


def load_catalog_frame() -> pd.DataFrame:
    return pd.read_csv(REFERENCE / "request_catalog.csv", dtype=str).fillna("")


def load_catalog() -> list[CatalogEntry]:
    out = []
    for r in load_catalog_frame().itertuples():
        out.append(CatalogEntry(
            code=r.request_type_code, label=r.citizen_label, family=r.complaint_family,
            complaint_types=tuple(r.complaint_type.split("|")),
            descriptor_regex=re.compile(r.descriptor_regex, re.I) if r.descriptor_regex else None,
            agency=r.agency, skill=r.required_skill, default_urgency=r.default_urgency,
            service_minutes=int(r.est_service_minutes),
        ))
    return out


def catalog_complaint_types() -> list[str]:
    return sorted({ct for e in load_catalog() for ct in e.complaint_types})


def match_catalog(complaint_type: str, descriptor: str | None, catalog: list[CatalogEntry]) -> CatalogEntry | None:
    """First entry whose complaint_type matches and whose descriptor regex matches
    (an empty regex is the catch-all for that complaint type). Order in the CSV matters."""
    d = descriptor or ""
    for e in catalog:
        if complaint_type in e.complaint_types and (e.descriptor_regex is None or e.descriptor_regex.search(d)):
            return e
    return None
