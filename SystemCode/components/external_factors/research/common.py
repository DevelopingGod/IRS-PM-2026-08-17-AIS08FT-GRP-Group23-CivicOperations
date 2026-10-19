"""Shared helpers for the historical research scripts.

Historical data are used ONLY for descriptive exploration, threshold sensitivity and hold-out
checks. There is no supervised training and no labelled proximity outcome. After-the-fact
fields (closed_date, resolution_description, outcome_class, verified_outcome, resolution_hours,
etc.) are dropped as soon as the data are loaded and are never used.

Split discipline (team-agreed time-based split, verified in explore_requests.py):
  train -> exploration and candidate-rule development
  val   -> comparison and selection of candidate configurations
  test  -> one final hold-out check of the frozen configuration (research/holdout_evaluation.py)
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_external_factors.osm_loader import load_facility_cache  # noqa: E402
from civicops_external_factors.schemas import LEAKAGE_FIELDS  # noqa: E402
from civicops_external_factors.spatial_engine import SpatialEngine  # noqa: E402

DATA = ROOT / "data" / "research" / "requests_real.parquet"
CATALOG = ROOT / "data" / "reference" / "request_catalog.csv"
OSM_DIR = ROOT / "data" / "osm"
TABLES = ROOT / "outputs" / "tables"
CHARTS = ROOT / "outputs" / "charts"
MAPS = ROOT / "outputs" / "maps"
METRIC_CRS = "EPSG:32118"

CORE = ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"]
ALL_CATS = CORE + ["TRANSIT_STATION"]

# Candidate radii (metres). Experimental choices spanning immediate surroundings to a broad
# neighbourhood. NOT official NYC thresholds. The operational meanings use Manhattan street-grid
# geometry: a "short" block is about 80 m (street to street), a "long" block about 270 m (avenue
# to avenue).
CANDIDATE_RADII = [50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000]

# Historical address_type -> location_precision used by the component's input contract.
ADDRESS_TYPE_TO_PRECISION = {"ADDRESS": "ADDRESS_POINT", "INTERSECTION": "INTERSECTION",
                             "BLOCKFACE": "BLOCKFACE", "PLACE": "PLACE", "PLACENAME": "PLACE"}

# Columns kept for research (all known at request creation time).
KEEP = ["request_id", "created_at", "split", "borough", "request_type_code", "request_type_label",
        "complaint_family", "complaint_type", "descriptor", "location_type", "address_type",
        "latitude", "longitude", "geo_valid", "community_board", "incident_zip"]


def load_requests(splits: tuple[str, ...] | None = None) -> pd.DataFrame:
    df = pd.read_parquet(DATA)
    leaked = [c for c in df.columns if c in LEAKAGE_FIELDS]
    df = df.drop(columns=leaked)
    df = df[[c for c in KEEP if c in df.columns]].copy()
    if splits is not None:
        df = df[df["split"].isin(splits)].copy()
    df["location_precision"] = df["address_type"].map(ADDRESS_TYPE_TO_PRECISION).fillna("UNKNOWN")
    df["geolocation_status"] = np.where(df["geo_valid"] & df["latitude"].notna(), "RESOLVED", "UNRESOLVED")
    for c in ("borough", "request_type_code", "complaint_family", "address_type", "location_type", "split"):
        df[c] = df[c].astype("string")
    return df.reset_index(drop=True)


def build_engine(categories=ALL_CATS) -> SpatialEngine:
    cache = load_facility_cache(OSM_DIR)
    return SpatialEngine(cache.facilities, cache.boroughs, METRIC_CRS, categories=categories)


def nearest_distances(df: pd.DataFrame, engine: SpatialEngine | None = None) -> pd.DataFrame:
    """Add nearest-facility distance (m) per category, plus nearest core distance and category."""
    engine = engine or build_engine()
    out = df.copy()
    valid = out["geolocation_status"].eq("RESOLVED").to_numpy()
    pts = engine.project(out.loc[valid, "latitude"].to_numpy(), out.loc[valid, "longitude"].to_numpy())
    for cat in ALL_CATS:
        col = np.full(len(out), np.nan)
        col[valid] = engine.nearest(pts, cat)[1]
        out[f"d_{cat}"] = col
    core_mat = out[[f"d_{c}" for c in CORE]].to_numpy()
    out["d_core"] = np.nanmin(np.where(np.isnan(core_mat), np.inf, core_mat), axis=1)
    out.loc[~valid, "d_core"] = np.nan
    arg = np.argmin(np.where(np.isnan(core_mat), np.inf, core_mat), axis=1)
    out["nearest_core_category"] = pd.Series(np.array(CORE)[arg]).where(valid)
    out["borough_from_coords"] = None
    b = engine.borough_of(pts)
    out.loc[valid, "borough_from_coords"] = b
    out["in_nyc_union"] = False
    out.loc[valid, "in_nyc_union"] = engine.in_coverage(pts, 0.0)
    return out


def trigger(dist_df: pd.DataFrame, thresholds: dict) -> pd.Series:
    """Point-estimate flag: any category with d <= threshold (NaN for invalid locations)."""
    valid = dist_df["geolocation_status"].eq("RESOLVED")
    flag = np.zeros(len(dist_df), dtype=bool)
    for cat, t in thresholds.items():
        flag |= (dist_df[f"d_{cat}"] <= t + 1e-6).fillna(False).to_numpy()
    return pd.Series(np.where(valid, flag.astype(float), np.nan), index=dist_df.index)


def cached_distances(splits: tuple[str, ...], tag: str) -> pd.DataFrame:
    """Compute (or load) the nearest-distance table for the given splits."""
    TABLES.mkdir(parents=True, exist_ok=True)
    path = TABLES / f"request_distances_{tag}.parquet"
    if path.exists():
        return pd.read_parquet(path)
    df = nearest_distances(load_requests(splits))
    df.to_parquet(path, index=False)
    return df
