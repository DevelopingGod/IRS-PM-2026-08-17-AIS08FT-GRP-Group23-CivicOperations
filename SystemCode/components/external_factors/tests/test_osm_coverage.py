"""OSM cache presence/coverage (17, 18) and real-cache integration checks."""

import sys
from pathlib import Path

import pytest

from conftest import REAL_CONFIG, REAL_OSM, ROOT, write_cache, write_config
from civicops_external_factors import CriticalFacilityEvaluator, OSMCacheError
from civicops_external_factors.schemas import BOROUGHS


def test_17_missing_osm_cache(tmp_path):
    cfg = write_config(tmp_path / "r.yaml")
    with pytest.raises(OSMCacheError, match="not found"):
        CriticalFacilityEvaluator.from_config(cfg, osm_data_dir=tmp_path / "does_not_exist")
    (tmp_path / "empty").mkdir()
    with pytest.raises(OSMCacheError, match="incomplete"):
        CriticalFacilityEvaluator.from_config(cfg, osm_data_dir=tmp_path / "empty")


def test_18_incomplete_borough_coverage(tmp_path):
    d = write_cache(tmp_path / "osm", boroughs=["MANHATTAN", "BROOKLYN", "BRONX", "STATEN ISLAND"])
    with pytest.raises(OSMCacheError, match="QUEENS"):
        CriticalFacilityEvaluator.from_config(write_config(tmp_path / "r.yaml"), osm_data_dir=d)


def test_18b_buffer_smaller_than_ruleset_requires(tmp_path):
    d = write_cache(tmp_path / "osm", buffer_m=200)
    with pytest.raises(OSMCacheError, match="buffer"):
        CriticalFacilityEvaluator.from_config(write_config(tmp_path / "r.yaml"), osm_data_dir=d)


def test_18c_ruleset_category_missing_from_cache(tmp_path):
    from conftest import DEFAULT_FACILITIES
    facs = [f for f in DEFAULT_FACILITIES if f["category"] != "HOSPITAL"]
    d = write_cache(tmp_path / "osm", facilities=facs)
    with pytest.raises(OSMCacheError, match="HOSPITAL"):
        CriticalFacilityEvaluator.from_config(write_config(tmp_path / "r.yaml"), osm_data_dir=d)


# ------------------------------------------------------------------ real OSM cache (integration)
pytestmark_real = pytest.mark.skipif(not (REAL_OSM / "facilities" / "critical_facilities.parquet").exists(),
                                     reason="real OSM cache not present")


@pytestmark_real
def test_real_cache_covers_all_five_boroughs(real_evaluator):
    assert set(real_evaluator.cache.boroughs["borough"]) == set(BOROUGHS)
    assert real_evaluator.cache.facility_buffer_m >= real_evaluator.ruleset.required_facility_buffer_m()


@pytestmark_real
def test_real_cache_validation_passes():
    sys.path.insert(0, str(ROOT / "osm_data_prep"))
    from validate_osm import validate
    res = validate(REAL_OSM, REAL_CONFIG, checksums=True)
    failed = [c for c in res["checks"] if not c["ok"] and not c["check"].startswith("road network")]
    assert res["ok"], failed
    for b in BOROUGHS:
        assert res["facilities_by_borough"][b].get("SCHOOL", 0) > 50


@pytestmark_real
def test_real_known_locations(real_evaluator):
    # Inside the Mount Sinai Hospital footprint (Manhattan) -> 1 with a HOSPITAL trigger at 0 m.
    r = real_evaluator.evaluate({"request_type": "RT005", "latitude": 40.7900, "longitude": -73.9530,
                                 "geolocation_status": "RESOLVED", "location_precision": "ADDRESS_POINT"})
    assert r.external_factor == 1
    assert any(f.facility_category == "HOSPITAL" and f.distance_m == 0 for f in r.triggering_facilities)
    # Open water of Jamaica Bay (inside Queens' administrative boundary) -> 0, complete.
    r2 = real_evaluator.evaluate({"request_type": "RT001", "latitude": 40.6100, "longitude": -73.8400,
                                  "geolocation_status": "RESOLVED", "location_precision": "ADDRESS_POINT"})
    assert r2.external_factor == 0 and r2.evaluation_status == "COMPLETE"
    assert r2.nearest_qualifying_facility.distance_m > 500


@pytestmark_real
def test_real_projected_distance_matches_geodesic(real_evaluator):
    eng = real_evaluator.engine
    import numpy as np
    rng = np.random.default_rng(1)
    lats = rng.uniform(40.55, 40.88, 200)
    lons = rng.uniform(-74.15, -73.75, 200)
    pts = eng.project(lats, lons)
    rows, dist = eng.nearest(pts, "SCHOOL")
    rel = []
    for p, r, d in zip(pts, rows, dist):
        if d > 50:
            rel.append(abs(d - eng.geodesic_distance_m(p, r)) / d)
    assert max(rel) < 1e-3   # projected EPSG:32118 within 0.1% of WGS84 geodesic
