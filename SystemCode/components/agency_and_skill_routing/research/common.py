"""Shared helpers for the offline research scripts.

The research phase is deliberately separated from the runtime package:

* Runtime (``civicops_agency``) depends only on the Python standard library and
  the shared request catalog.
* Research (this folder) depends on pandas/pyarrow and the historical Parquet
  file, which is *not* needed to run the router.

Every research script writes its evidence (CSV tables and a JSON summary
fragment) to ``outputs/research`` so that the HTML report is generated from
executed results rather than hand-copied numbers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict

import pandas as pd

# ---------------------------------------------------------------------------
# Portable paths. Everything is resolved relative to the package root, and can
# be overridden with environment variables so the scripts run from a clean
# extracted ZIP without editing code.
# ---------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = Path(os.environ.get("CIVICOPS_CATALOG", ROOT / "data" / "reference" / "request_catalog.csv"))
MATRIX_PATH = Path(os.environ.get("CIVICOPS_MATRIX", ROOT / "data" / "reference" / "NYC311_agency_matrix.md"))
PARQUET_PATH = Path(os.environ.get("CIVICOPS_HISTORY", ROOT / "data" / "research" / "requests_real.parquet"))
MATRIX_MAP_PATH = ROOT / "research" / "matrix_family_map.csv"
OUT_DIR = ROOT / "outputs" / "research"
KNOWLEDGE_OUT = ROOT / "data" / "reference" / "referral_knowledge.json"

# Columns that only exist after a request has been handled. They are allowed
# for retrospective analysis (e.g. understanding referrals) but must never be
# used as runtime inputs or as features of anything shipped to the router.
POST_OUTCOME_COLUMNS = [
    "closed_date",
    "resolution_description",
    "resolution_action_updated_date",
    "status",
    "outcome_class",
    "verified_outcome",
    "resolution_hours",
    "log_resolution_hours",
    "resolution_valid",
]

# Fields that the CO1 contract actually supplies at runtime. Historical columns
# outside this list (complaint_type, descriptor, location_type, coordinates)
# can be studied offline but cannot drive the operational router.
RUNTIME_AVAILABLE_FIELDS = ["request_type_code", "borough"]

SPLIT_ORDER = ["train", "val", "test"]


def ensure_out_dir() -> Path:
    """Create the research output directory if needed and return it."""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    return OUT_DIR


def load_history() -> pd.DataFrame:
    """Load the historical dataset, failing with a clear message if absent.

    The Parquet file is a research-only dependency, so a missing file is an
    expected situation in the runtime distribution and is reported, not hidden.
    """
    if not PARQUET_PATH.exists():
        raise FileNotFoundError(
            f"Historical dataset not found at {PARQUET_PATH}. Supply it separately "
            "(see README, 'Research package') or set CIVICOPS_HISTORY."
        )
    df = pd.read_parquet(PARQUET_PATH)
    # Normalise pandas extension dtypes to plain objects so that groupby and
    # CSV export behave identically across pandas versions.
    for col in df.columns:
        if str(df[col].dtype) in ("string", "str") or str(df[col].dtype).startswith("string"):
            df[col] = df[col].astype(object)
    return df


def load_catalog() -> pd.DataFrame:
    """Load the shared catalog exactly as supplied (all columns kept as text)."""
    return pd.read_csv(CATALOG_PATH, dtype=str, keep_default_na=False)


def load_matrix_map() -> pd.DataFrame:
    """Load the analyst annotation linking each RT code to an agency-matrix family."""
    return pd.read_csv(MATRIX_MAP_PATH, dtype=str, keep_default_na=False)


def write_table(df: pd.DataFrame, name: str) -> Path:
    """Write a full evidence table (no truncation) to ``outputs/research``."""
    path = ensure_out_dir() / name
    df.to_csv(path, index=False)
    return path


def update_summary(section: str, payload: Dict[str, Any]) -> None:
    """Merge a section into ``outputs/research/summary.json``.

    Each script owns one section, so the scripts can run independently and in
    any order while the report always reads a single consolidated file.
    """
    path = ensure_out_dir() / "summary.json"
    data: Dict[str, Any] = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    data[section] = payload
    path.write_text(json.dumps(data, indent=2, default=_json_default), encoding="utf-8")


def _json_default(obj: Any) -> Any:
    """Make numpy/pandas scalars JSON serialisable."""
    if hasattr(obj, "item"):
        return obj.item()
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    return str(obj)
