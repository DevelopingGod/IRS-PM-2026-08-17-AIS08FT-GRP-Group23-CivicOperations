"""Offline loading and validation of the local OSM cache (facilities + borough boundaries).

Runtime evaluation reads only local files. Nothing here touches the network.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

import geopandas as gpd
import pandas as pd

from .schemas import BOROUGHS

FACILITY_PARQUET = Path("facilities") / "critical_facilities.parquet"
FACILITY_GPKG = Path("facilities") / "critical_facilities.gpkg"
BOROUGHS_GPKG = Path("boundaries") / "nyc_boroughs.gpkg"
COVERAGE_GPKG = Path("boundaries") / "nyc_coverage.gpkg"
MANIFEST = Path("manifest.json")

REQUIRED_FACILITY_COLUMNS = ("facility_id", "osm_type", "osm_id", "category", "name",
                             "geometry_type", "is_duplicate", "geometry")


class OSMCacheError(RuntimeError):
    """Raised when the local OSM cache is missing, incomplete or inconsistent."""


@dataclass
class FacilityCache:
    data_dir: Path
    facilities: gpd.GeoDataFrame          # EPSG:4326, original geometries
    boroughs: gpd.GeoDataFrame            # EPSG:4326
    facility_buffer_m: float
    manifest: dict = field(default_factory=dict)
    issues: list = field(default_factory=list)

    @property
    def data_version(self) -> str:
        return str(self.manifest.get("data_version", "unknown"))


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def missing_runtime_files(data_dir: str | Path) -> list[str]:
    d = Path(data_dir)
    missing = []
    if not (d / FACILITY_PARQUET).exists() and not (d / FACILITY_GPKG).exists():
        missing.append(str(FACILITY_PARQUET))
    for p in (BOROUGHS_GPKG, COVERAGE_GPKG):
        if not (d / p).exists():
            missing.append(str(p))
    return missing


def load_facility_cache(data_dir: str | Path) -> FacilityCache:
    """Load facilities, borough boundaries and manifest from ``data_dir``. Raises OSMCacheError."""
    d = Path(data_dir)
    if not d.exists():
        raise OSMCacheError(f"OSM data directory not found: {d}. Run osm_data_prep/download_osm.py "
                            f"or unpack the shared OSM data bundle into this location.")
    missing = missing_runtime_files(d)
    if missing:
        raise OSMCacheError(f"OSM cache in {d} is incomplete; missing: {', '.join(missing)}")
    issues: list = []
    try:
        if (d / FACILITY_PARQUET).exists():
            fac = gpd.read_parquet(d / FACILITY_PARQUET)
        else:
            fac = gpd.read_file(d / FACILITY_GPKG, layer="facilities")
            issues.append("facilities loaded from GeoPackage (GeoParquet copy missing)")
        boroughs = gpd.read_file(d / BOROUGHS_GPKG, layer="boroughs")
        search_area = gpd.read_file(d / COVERAGE_GPKG, layer="facility_search_area")
    except Exception as exc:  # corrupted / unreadable files
        raise OSMCacheError(f"Could not read OSM cache in {d}: {exc}") from exc

    missing_cols = [c for c in REQUIRED_FACILITY_COLUMNS if c not in fac.columns]
    if missing_cols:
        raise OSMCacheError(f"Facility table lacks required columns: {missing_cols}")
    if fac.crs is None or boroughs.crs is None:
        raise OSMCacheError("OSM cache layers must carry a CRS")
    fac = fac.to_crs("EPSG:4326")
    boroughs = boroughs.to_crs("EPSG:4326")

    present = set(boroughs["borough"].astype(str).str.upper())
    absent = [b for b in BOROUGHS if b not in present]
    if absent:
        raise OSMCacheError(f"Borough coverage incomplete; missing boroughs: {absent}")
    empty = boroughs[boroughs.geometry.is_empty | boroughs.geometry.isna()]
    if len(empty):
        raise OSMCacheError(f"Empty borough geometry for: {list(empty['borough'])}")

    buffer_m = float(search_area["buffer_m"].iloc[0]) if "buffer_m" in search_area.columns else 0.0
    manifest = {}
    if (d / MANIFEST).exists():
        manifest = json.loads((d / MANIFEST).read_text("utf-8"))
    else:
        issues.append("manifest.json missing (run osm_data_prep/build_manifest.py)")

    bad = fac.geometry.isna() | fac.geometry.is_empty
    if bad.any():
        issues.append(f"{int(bad.sum())} facilities with missing/empty geometry dropped at load")
        fac = fac[~bad]
    invalid = ~fac.geometry.is_valid
    if invalid.any():
        issues.append(f"{int(invalid.sum())} invalid facility geometries repaired at load (make_valid)")
        fac = fac.copy()
        fac.loc[invalid, "geometry"] = fac.loc[invalid, "geometry"].make_valid()
    fac = fac.reset_index(drop=True)
    return FacilityCache(d, fac, boroughs, buffer_m, manifest, issues)


def validate_cache_files(data_dir: str | Path, verify_checksums: bool = True) -> dict:
    """File-level validation against the manifest (presence, sizes, checksums)."""
    d = Path(data_dir)
    report = {"data_dir": str(d), "missing_runtime_files": missing_runtime_files(d), "files": [],
              "manifest_present": (d / MANIFEST).exists(), "ok": True}
    if report["missing_runtime_files"]:
        report["ok"] = False
    if not report["manifest_present"]:
        report["ok"] = False
        return report
    manifest = json.loads((d / MANIFEST).read_text("utf-8"))
    for entry in manifest.get("files", []):
        p = d / entry["path"]
        row = {"path": entry["path"], "present": p.exists(), "required_for": entry.get("required_for")}
        if p.exists():
            row["size_ok"] = p.stat().st_size == entry.get("size_bytes")
            if verify_checksums and entry.get("sha256"):
                row["checksum_ok"] = sha256_file(p) == entry["sha256"]
        ok = row["present"] and row.get("size_ok", True) and row.get("checksum_ok", True)
        if not ok and entry.get("required_for") == "runtime":
            report["ok"] = False
        row["ok"] = ok
        report["files"].append(row)
    return report


def facilities_summary(fac: pd.DataFrame) -> pd.DataFrame:
    """Counts by category x geometry type x duplicate flag."""
    return (fac.groupby(["category", "geometry_type", "is_duplicate"]).size()
            .rename("count").reset_index())


def borough_of_points(boroughs: gpd.GeoDataFrame, points: gpd.GeoSeries) -> pd.Series:
    j = gpd.sjoin(gpd.GeoDataFrame(geometry=points, crs=points.crs),
                  boroughs[["borough", "geometry"]].to_crs(points.crs), how="left", predicate="within")
    j = j[~j.index.duplicated(keep="first")]
    return j["borough"].reindex(points.index)


def optional_road_files(data_dir: str | Path) -> dict:
    d = Path(data_dir) / "roads"
    return {name: (d / name).exists() for name in ("nyc_drive.graphml", "nyc_drive.gpkg")}
