"""Spatial engine and geometry scenarios on the synthetic cache (exact expected distances).

Scenario numbers refer to the mandatory test list in README.md / the report.
"""

import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from conftest import T, _fac, DEFAULT_FACILITIES, METRIC, req, write_cache, write_config
from civicops_external_factors import CriticalFacilityEvaluator
from civicops_external_factors.facility_catalog import mark_duplicates
from civicops_external_factors.osm_loader import load_facility_cache


def test_01_inside_radius(evaluator):
    r = evaluator.evaluate(req(1000, 920))
    assert r.external_factor == 1
    assert r.triggering_facility_count == 1
    f = r.triggering_facilities[0]
    assert f.facility_category == "SCHOOL" and f.distance_m == pytest.approx(80, abs=1e-3)


def test_02_outside_radius(evaluator):
    r = evaluator.evaluate(req(1000, 850))
    assert r.external_factor == 0 and r.evaluation_status == "COMPLETE"
    assert r.nearest_qualifying_facility.distance_m == pytest.approx(150, abs=1e-3)
    assert r.triggered_rules[0] == "CRITICAL_PROXIMITY_NONE"


def test_03_exactly_at_threshold_is_inclusive(evaluator):
    assert evaluator.evaluate(req(1000, 900)).external_factor == 1          # 100.000 m
    assert evaluator.evaluate(req(1000, 899.99)).external_factor == 0       # 100.01 m


def test_04_inside_facility_polygon_distance_zero(evaluator):
    r = evaluator.evaluate(req(1550, 550))
    assert r.external_factor == 1
    f = r.triggering_facilities[0]
    assert f.facility_category == "HOSPITAL" and f.distance_m == 0.0
    assert "inside its mapped footprint" in r.explanation


def test_05_polygon_boundary_not_centroid(evaluator):
    # 100 m from the polygon edge but 150 m from its centroid: the boundary distance decides.
    r = evaluator.evaluate(req(1700, 550))
    assert r.external_factor == 1
    assert r.triggering_facilities[0].distance_m == pytest.approx(100, abs=1e-3)
    assert evaluator.evaluate(req(1700.5, 550)).external_factor == 0


def test_06_multiple_nearby_facilities_sorted(evaluator):
    r = evaluator.evaluate(req(1000, 1030))
    assert r.triggering_facility_count == 2
    d = [f.distance_m for f in r.triggering_facilities]
    assert d == sorted(d) and d[0] == pytest.approx(30, abs=1e-3)


def test_07_multiple_categories_each_rule_reported(evaluator):
    r = evaluator.evaluate(req(1000, 1030))
    assert {"CRITICAL_PROXIMITY_SCHOOL", "CRITICAL_PROXIMITY_CHILDCARE"} <= set(r.triggered_rules)


def test_08_nearest_facility_not_qualifying(evaluator):
    r = evaluator.evaluate(req(500, 1450))          # transit station 50 m away (exploratory)
    assert r.external_factor == 0
    assert r.nearest_facility_overall.facility_category == "TRANSIT_STATION"
    assert r.nearest_facility_overall.qualifies is False
    assert r.nearest_qualifying_facility.facility_category != "TRANSIT_STATION"
    assert r.nearest_qualifying_facility.distance_m > 100


def test_09_no_nearby_facilities(evaluator):
    r = evaluator.evaluate(req(5000, 1000))
    assert r.external_factor == 0 and r.triggering_facilities == []
    assert r.nearest_qualifying_facility.distance_m > 1000


def test_10_facility_across_borough_boundary(evaluator):
    r = evaluator.evaluate(req(1960, 1000, borough="MANHATTAN"))   # fire station 90 m away in BROOKLYN
    assert r.external_factor == 1
    assert r.triggering_facilities[0].facility_category == "FIRE_STATION"
    assert r.location_quality.borough_from_coordinates == "MANHATTAN"
    assert not any(w.startswith("BOROUGH_MISMATCH") for w in r.warnings)


def test_11_near_coverage_boundary_sees_facility_outside_nyc(evaluator):
    r = evaluator.evaluate(req(-60, 1000))          # inside coverage tolerance; police 90 m away outside NYC
    assert r.external_factor == 1
    assert r.triggering_facilities[0].facility_category == "POLICE"
    far = evaluator.evaluate(req(-300, 1000))       # beyond tolerance -> outside coverage
    assert far.external_factor is None and far.incomplete_reason == "OUTSIDE_COVERAGE"


def test_19_invalid_facility_geometry_is_repaired(tmp_path):
    bowtie = Polygon([(4500, 500), (4600, 600), (4600, 500), (4500, 600)])
    assert not bowtie.is_valid
    facs = DEFAULT_FACILITIES + [_fac("way/99", "HOSPITAL", T(bowtie), "Bowtie Hospital")]
    d = write_cache(tmp_path / "osm", facilities=facs)
    cache = load_facility_cache(d)
    assert any("repaired" in i for i in cache.issues)
    assert cache.facilities.geometry.is_valid.all()
    ev = CriticalFacilityEvaluator.from_config(write_config(tmp_path / "r.yaml"), osm_data_dir=d)
    r = ev.evaluate(req(4510, 550))                 # inside the left triangle of the repaired bowtie
    assert r.external_factor == 1 and r.triggering_facilities[0].distance_m == 0.0


def test_19b_empty_geometry_dropped(tmp_path):
    from shapely.geometry import Point as P
    facs = DEFAULT_FACILITIES + [_fac("node/98", "SCHOOL", P(), "Empty")]
    d = write_cache(tmp_path / "osm", facilities=facs, mark_dups=False)
    cache = load_facility_cache(d)
    assert "node/98" not in set(cache.facilities.facility_id)


def test_20_duplicate_osm_representations(evaluator):
    fac = evaluator.cache.facilities.set_index("facility_id")
    assert bool(fac.loc["node/8", "is_duplicate"]) and fac.loc["node/8", "duplicate_of"] == "way/7"
    r = evaluator.evaluate(req(7050, 1050))
    assert r.triggering_facility_count == 1 and r.triggering_facilities[0].facility_id == "way/7"


def test_20b_mark_duplicates_rules():
    g = gpd.GeoDataFrame(DEFAULT_FACILITIES, geometry="geometry", crs=METRIC)
    out, counts = mark_duplicates(g, METRIC)
    assert counts["D1_point_in_polygon"] == 1 and counts["total_duplicates"] == 1
    # different names are not merged
    g2 = g.copy()
    g2.loc[g2.facility_id == "node/8", "name"] = "Another School"
    _, c2 = mark_duplicates(g2, METRIC)
    assert c2["total_duplicates"] == 0


def test_spatial_index_matches_brute_force(evaluator):
    eng = evaluator.engine
    reqs = [req(x, y) for x, y in [(1000, 920), (5000, 1000), (1960, 1000)]]
    pts = eng.project([r["latitude"] for r in reqs], [r["longitude"] for r in reqs])
    for cat in eng.indexes:
        rows, dist = eng.nearest(pts, cat)
        for k, p in enumerate(pts):
            assert dist[k] == pytest.approx(eng.brute_force_distances(p, cat).min(), abs=1e-6)
