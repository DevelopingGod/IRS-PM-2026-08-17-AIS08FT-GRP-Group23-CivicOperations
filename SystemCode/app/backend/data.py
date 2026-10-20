"""Reference data the app needs, loaded once.

Nothing here makes a decision. It supplies the shared catalog, the crews, the evaluation
scenarios the remove one measurement runs on, and the real NYC 311 track record of the
synthetic reporters linked to imported backlog requests.
"""
from __future__ import annotations

import csv
from datetime import datetime
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
REF = ROOT / "data" / "reference"
PROC = ROOT / "data" / "processed"


@lru_cache(maxsize=1)
def catalog() -> dict[str, dict]:
    with (REF / "request_catalog.csv").open(encoding="utf-8") as f:
        return {r["request_type_code"]: r for r in csv.DictReader(f)}


SLA_DIR = REF / "sla"     # built by 06_Workspace_Tools/build_sla_mapping.py from Khalid's official SLA table


@lru_cache(maxsize=1)
def _sla_defaults() -> dict[str, dict]:
    try:
        with (SLA_DIR / "sla_defaults.csv").open(encoding="utf-8") as f:
            return {r["request_type_code"]: r for r in csv.DictReader(f)}
    except FileNotFoundError:
        return {}


def sla_category(request_type: str | None) -> dict:
    """The official NYC 311 category for a request type, which Prioritisation needs to find its SLA."""
    r = _sla_defaults().get(request_type or "")
    if not r:
        return {}
    return {k: r[k] for k in ("official_agency", "official_problem", "official_problem_details", "official_additional_details")}


@lru_cache(maxsize=1)
def evaluation_scenarios() -> list[dict]:
    """The 34 hand written cases the remove one measurement runs on. Never filed as real requests."""
    with (REF / "demo_scenarios.csv").open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=1)
def crews() -> dict[str, dict]:
    try:
        c = pd.read_parquet(PROC / "synthetic_crews.parquet")
    except Exception:
        return {}
    return {r.crew_id: {"crew_id": r.crew_id, "agency": r.agency, "borough": r.borough, "skills": list(r.skills),
                        "depot_lat": float(r.depot_lat), "depot_lon": float(r.depot_lon)}
            for r in c.itertuples()}


@lru_cache(maxsize=1)
def _reporter_outcomes() -> pd.DataFrame:
    try:
        links = pd.read_parquet(PROC / "synthetic_reporter_links.parquet")
        real = pd.read_parquet(PROC / "requests_real.parquet",
                               columns=["request_id", "verified_outcome", "created_at", "request_type_code"])
    except Exception:
        return pd.DataFrame(columns=["citizen_id", "request_id", "verified_outcome", "created_at", "request_type_code"])
    h = links.merge(real, on="request_id")
    return h[h.verified_outcome.isin(["GENUINE", "UNFOUNDED"])]


def reporter_history(citizen_id: str, exclude_request: str | None = None) -> list[dict]:
    """Real NYC 311 outcomes of the synthetic reporter behind an imported request."""
    h = _reporter_outcomes()
    rows = h[(h.citizen_id == citizen_id) & (h.request_id != exclude_request)]
    return [{"outcome": o, "closed_at": pd.Timestamp(d).to_pydatetime().replace(tzinfo=None), "request_type": rt}
            for o, d, rt in zip(rows.verified_outcome, rows.created_at, rows.request_type_code)]


@lru_cache(maxsize=1)
def typical_reporter() -> str | None:
    """A reporter whose confirmation rate is the median, for the remove one measurement."""
    h = _reporter_outcomes()
    if h.empty:
        return None
    s = h.groupby("citizen_id").agg(n=("request_id", "size"), rate=("verified_outcome", lambda x: (x == "GENUINE").mean()))
    s = s[s.n >= 8]
    return str(s.iloc[(s.rate - s.rate.median()).abs().argsort()].index[0]) if len(s) else None


def now_new_york() -> datetime:
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo("America/New_York"))
