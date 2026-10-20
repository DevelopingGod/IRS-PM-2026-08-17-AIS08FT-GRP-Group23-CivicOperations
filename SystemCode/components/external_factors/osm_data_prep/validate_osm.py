"""Validate the local OSM cache: files, checksums, borough coverage, geometry and ruleset buffer.

Usage: python osm_data_prep/validate_osm.py [--data-dir data/osm] [--config config/proximity_rules.yaml]
                                            [--no-checksums] [--json outputs/tables/osm_validation.json]
Exit code 0 when the cache is valid for runtime evaluation, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402
import shapely  # noqa: E402

from civicops_external_factors.osm_loader import (OSMCacheError, load_facility_cache,  # noqa: E402
                                                  optional_road_files, validate_cache_files)
from civicops_external_factors.proximity_rules import ProximityRuleset  # noqa: E402
from civicops_external_factors.schemas import BOROUGHS  # noqa: E402


def validate(data_dir: Path, config: Path, checksums: bool = True) -> dict:
    out = {"data_dir": str(data_dir), "checks": []}

    def check(name, ok, detail=""):
        out["checks"].append({"check": name, "ok": bool(ok), "detail": detail})

    files = validate_cache_files(data_dir, verify_checksums=checksums)
    check("runtime files present", not files["missing_runtime_files"], ", ".join(files["missing_runtime_files"]))
    check("manifest present", files["manifest_present"])
    bad = [f["path"] for f in files["files"] if not f["ok"]]
    check("manifest sizes/checksums match", not bad, ", ".join(bad))
    try:
        cache = load_facility_cache(data_dir)
    except OSMCacheError as exc:
        check("cache loads", False, str(exc))
        out["ok"] = False
        return out
    check("cache loads", True, "; ".join(cache.issues))
    present = set(cache.boroughs["borough"])
    check("all five boroughs present", all(b in present for b in BOROUGHS), f"present={sorted(present)}")
    fac = cache.facilities
    live = fac[~fac["is_duplicate"]]
    m = live.to_crs("EPSG:32118")
    bm = cache.boroughs.to_crs("EPSG:32118")
    per_borough = {}
    for b in bm.itertuples():
        inside = m[m.geometry.representative_point().within(b.geometry)]
        per_borough[b.borough] = inside["category"].value_counts().to_dict()
    out["facilities_by_borough"] = per_borough
    core = [c for c in live["category"].unique() if live.loc[live.category == c, "category_status"].iloc[0] == "core"]
    gaps = [(b, c) for b in per_borough for c in core if per_borough[b].get(c, 0) == 0]
    check("every core category present in every borough", not gaps, str(gaps))
    check("no invalid facility geometries", bool(fac.geometry.is_valid.all()))
    check("no empty facility geometries", not bool(fac.geometry.is_empty.any()))
    union = shapely.union_all(bm.geometry.values)
    search = gpd.read_file(data_dir / "boundaries" / "nyc_coverage.gpkg", layer="facility_search_area").to_crs("EPSG:32118")
    check("facility search area contains NYC", bool(search.geometry.iloc[0].contains(union)))
    # Overpass returns whole features that intersect the query polygon, so large campuses may extend past it.
    disjoint = m[~m.geometry.intersects(search.geometry.iloc[0])]
    partial = int((~m.geometry.within(search.geometry.iloc[0])).sum()) - len(disjoint)
    check("facilities intersect the search area", len(disjoint) == 0,
          f"{len(disjoint)} disjoint; {partial} extend beyond the search-area edge (kept whole)")
    rs = ProximityRuleset.from_yaml(config)
    need = rs.required_facility_buffer_m()
    check("facility buffer covers ruleset", cache.facility_buffer_m >= need,
          f"buffer={cache.facility_buffer_m:g} m, required={need:g} m")
    missing_cats = [c for c in rs.category_rules if c not in set(live["category"])]
    check("ruleset categories present in cache", not missing_cats, str(missing_cats))
    roads = optional_road_files(data_dir)
    out["roads_files"] = roads
    check("road network present (optional, for Crew Dispatch)", all(roads.values()), str(roads))
    out["ok"] = all(c["ok"] for c in out["checks"] if not c["check"].startswith("road network"))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "osm"))
    ap.add_argument("--config", default=str(ROOT / "config" / "proximity_rules.yaml"))
    ap.add_argument("--no-checksums", action="store_true")
    ap.add_argument("--json", help="write the validation report to this path")
    args = ap.parse_args(argv)
    res = validate(Path(args.data_dir), Path(args.config), not args.no_checksums)
    for c in res["checks"]:
        print(f"[{'PASS' if c['ok'] else 'FAIL'}] {c['check']}" + (f"  ({c['detail']})" if c["detail"] else ""))
    print("CACHE VALID FOR OFFLINE EVALUATION" if res["ok"] else "CACHE NOT VALID")
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(res, indent=2, default=str), "utf-8")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
