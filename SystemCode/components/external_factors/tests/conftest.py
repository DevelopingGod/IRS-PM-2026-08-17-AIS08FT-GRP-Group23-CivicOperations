"""Shared fixtures: a tiny synthetic OSM cache (exact geometry, known distances) and the real cache.

Synthetic world (EPSG:32118 metres, origin X0/Y0 placed in NYC so coordinates are realistic):
five 2 km x 2 km "boroughs" in a row along x:
    MANHATTAN [0,2000)  BROOKLYN [2000,4000)  QUEENS [4000,6000)  BRONX [6000,8000)  STATEN ISLAND [8000,10000)
Facilities (local metres):
    S1  SCHOOL point           (1000, 1000)
    H1  HOSPITAL polygon       x 1500..1600, y 500..600
    F1  FIRE_STATION point     (2050, 1000)        just inside BROOKLYN, 50 m east of the boundary
    T1  TRANSIT_STATION point  (500, 1500)         exploratory (non-qualifying)
    P1  POLICE point           (-150, 1000)        OUTSIDE the five boroughs (in the buffer)
    C1  CHILDCARE point        (1000, 1060)        60 m north of S1
    S2  SCHOOL polygon         x 7000..7100, y 1000..1100 (BRONX); its POI node is a duplicate
    S2n SCHOOL point           (7050, 1050)        duplicate of S2 (inside polygon, same name)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import geopandas as gpd
import pytest
import shapely
from pyproj import Transformer
from shapely.geometry import Point, box

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_external_factors.facility_catalog import mark_duplicates  # noqa: E402

METRIC = "EPSG:32118"
X0, Y0 = 300000.0, 60000.0
_TO_WGS = Transformer.from_crs(METRIC, "EPSG:4326", always_xy=True)
BOROUGH_NAMES = ["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"]
REAL_CONFIG = ROOT / "config" / "proximity_rules.yaml"
REAL_OSM = ROOT / "data" / "osm"


def latlon(x: float, y: float) -> tuple[float, float]:
    lon, lat = _TO_WGS.transform(X0 + x, Y0 + y)
    return lat, lon


def _fac(fid, cat, geom, name, status="core"):
    otype, oid = fid.split("/")
    return {"facility_id": fid, "osm_type": otype, "osm_id": int(oid), "category": cat,
            "category_status": status, "name": name, "geometry_type": geom.geom_type, "borough": None,
            "is_duplicate": False, "duplicate_of": None, "geometry_repaired": False,
            "tags_json": json.dumps({"name": name}), "acquired_at": "synthetic", "geometry": geom}


def T(geom):
    return shapely.affinity.translate(geom, X0, Y0)


DEFAULT_FACILITIES = [
    _fac("node/1", "SCHOOL", T(Point(1000, 1000)), "Synthetic School S1"),
    _fac("way/2", "HOSPITAL", T(box(1500, 500, 1600, 600)), "Synthetic Hospital H1"),
    _fac("node/3", "FIRE_STATION", T(Point(2050, 1000)), "Synthetic Fire F1"),
    _fac("node/4", "TRANSIT_STATION", T(Point(500, 1500)), "Synthetic Station T1", "exploratory"),
    _fac("node/5", "POLICE", T(Point(-150, 1000)), "Synthetic Police P1 (outside NYC)"),
    _fac("node/6", "CHILDCARE", T(Point(1000, 1060)), "Synthetic Childcare C1"),
    _fac("way/7", "SCHOOL", T(box(7000, 1000, 7100, 1100)), "Synthetic School S2"),
    _fac("node/8", "SCHOOL", T(Point(7050, 1050)), "Synthetic School S2"),
    _fac("node/9", "SENIOR_CARE", T(Point(9000, 1900)), "Synthetic Senior Home"),
]


def write_cache(d: Path, facilities=None, boroughs=BOROUGH_NAMES, buffer_m=2000.0, mark_dups=True) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "facilities").mkdir(exist_ok=True)
    (d / "boundaries").mkdir(exist_ok=True)
    rows = [{"borough": b, "osm_id": i, "display_name": b, "geometry": T(box(2000 * i, 0, 2000 * (i + 1), 2000))}
            for i, b in enumerate(BOROUGH_NAMES) if b in boroughs]
    bgdf = gpd.GeoDataFrame(rows, geometry="geometry", crs=METRIC)
    bgdf.to_crs("EPSG:4326").to_file(d / "boundaries" / "nyc_boroughs.gpkg", layer="boroughs", driver="GPKG")
    union = bgdf.geometry.union_all()
    gpd.GeoDataFrame({"layer": ["facility_search_area"], "buffer_m": [buffer_m]},
                     geometry=[union.buffer(buffer_m)], crs=METRIC).to_crs("EPSG:4326").to_file(
        d / "boundaries" / "nyc_coverage.gpkg", layer="facility_search_area", driver="GPKG")
    fac = gpd.GeoDataFrame(facilities if facilities is not None else DEFAULT_FACILITIES, geometry="geometry", crs=METRIC)
    if mark_dups:
        fac, _ = mark_duplicates(fac, METRIC)
    fac.to_crs("EPSG:4326").to_parquet(d / "facilities" / "critical_facilities.parquet", index=False)
    (d / "manifest.json").write_text(json.dumps({"data_version": "synthetic-test", "files": []}), "utf-8")
    return d


def write_config(path: Path, threshold=100, mode="PURE_GEOGRAPHIC", extra: dict | None = None) -> Path:
    import yaml
    cfg = {
        "ruleset_version": "test", "decision_mode": mode, "metric_crs": METRIC,
        "request_catalog_path": str(ROOT / "data" / "reference" / "request_catalog.csv"),
        "facility_categories": {c: {"threshold_m": threshold} for c in
                                ["HOSPITAL", "SCHOOL", "CHILDCARE", "SENIOR_CARE", "FIRE_STATION", "POLICE"]},
        "evidence": {"nearest_overall_categories": ["TRANSIT_STATION"], "max_facilities_listed": 10},
        "location": {"accepted_geolocation_status": ["RESOLVED", "APPROXIMATE"],
                     "precision_aliases": {"ROOFTOP": "ADDRESS_POINT"},
                     "default_uncertainty_m": {"ADDRESS_POINT": 15, "GPS_DEVICE": 20, "INTERSECTION": 25,
                                               "BLOCKFACE": 120, "PLACE": 150, "UNKNOWN": 100},
                     "unsupported_precision": ["POSTAL_CODE", "NEIGHBORHOOD", "BOROUGH", "CITY"],
                     "point_decision_max_uncertainty_m": 30, "max_evaluable_uncertainty_m": 250,
                     "request_coverage_tolerance_m": 100},
        "context_aware": {"not_applicable_request_types": ["RT080", "RT081", "RT082", "RT083", "RT084", "RT141"]},
    }
    if extra:
        for k, v in extra.items():
            cfg[k] = v
    path.write_text(yaml.safe_dump(cfg, sort_keys=False), "utf-8")
    return path


def req(x, y, **kw):
    lat, lon = latlon(x, y)
    base = {"request_id": kw.pop("request_id", "test"), "request_type": kw.pop("request_type", "RT005"),
            "latitude": lat, "longitude": lon, "geolocation_status": "RESOLVED",
            "location_precision": "ADDRESS_POINT", "location_uncertainty_m": 0}
    base.update(kw)
    return base


@pytest.fixture
def synth_dir(tmp_path):
    return write_cache(tmp_path / "osm")


@pytest.fixture
def synth_config(tmp_path):
    return write_config(tmp_path / "rules.yaml")


@pytest.fixture
def evaluator(synth_dir, synth_config):
    from civicops_external_factors import CriticalFacilityEvaluator
    return CriticalFacilityEvaluator.from_config(synth_config, osm_data_dir=synth_dir)


@pytest.fixture(scope="session")
def real_evaluator():
    if not (REAL_OSM / "facilities" / "critical_facilities.parquet").exists():
        pytest.skip("real OSM cache not present")
    from civicops_external_factors import CriticalFacilityEvaluator
    return CriticalFacilityEvaluator.from_config(REAL_CONFIG)

