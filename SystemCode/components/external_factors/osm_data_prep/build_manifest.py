"""Build data/osm/manifest.json describing the cached OSM data package.

Records source, acquisition dates and method, borough coverage, buffers, facility counts by
category and geometry type, road-network size, CRS, file sizes and SHA-256 checksums.

Usage: python osm_data_prep/build_manifest.py [--data-dir data/osm]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402
import yaml  # noqa: E402

from civicops_external_factors.osm_loader import sha256_file  # noqa: E402

RUNTIME_FILES = {"facilities/critical_facilities.parquet", "facilities/critical_facilities.gpkg",
                 "boundaries/nyc_boroughs.gpkg", "boundaries/nyc_coverage.gpkg"}


def file_entries(data_dir: Path) -> list[dict]:
    out = []
    for p in sorted(data_dir.rglob("*")):
        if not p.is_file() or "_http_cache" in p.parts or p.name == "manifest.json":
            continue
        rel = p.relative_to(data_dir).as_posix()
        out.append({"path": rel, "size_bytes": p.stat().st_size, "sha256": sha256_file(p),
                    "required_for": "runtime" if rel in RUNTIME_FILES else
                    ("crew_dispatch" if rel.startswith("roads/") else "documentation")})
    return out


def road_summary(data_dir: Path) -> dict:
    gpkg = data_dir / "roads" / "nyc_drive.gpkg"
    if not gpkg.exists():
        return {"present": False}
    nodes = gpd.read_file(gpkg, layer="nodes", columns=[])
    edges = gpd.read_file(gpkg, layer="edges", ignore_geometry=True)
    attr_cov = {c: round(float(edges[c].notna().mean()), 4) for c in
                ("name", "highway", "oneway", "lanes", "maxspeed", "length", "bridge", "tunnel", "ref", "access")
                if c in edges.columns}
    return {"present": True, "nodes": int(len(nodes)), "edges": int(len(edges)), "crs": str(nodes.crs),
            "directed": True, "edge_attribute_coverage": attr_cov,
            "total_length_km": round(float(edges["length"].sum()) / 1000, 1) if "length" in edges else None,
            "files": ["roads/nyc_drive.graphml", "roads/nyc_drive.gpkg"]}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "osm"))
    args = ap.parse_args(argv)
    d = Path(args.data_dir)
    settings = yaml.safe_load(open(ROOT / "config" / "osm_data.yaml", encoding="utf-8"))
    report = json.loads((d / "acquisition_report.json").read_text("utf-8")) if (d / "acquisition_report.json").exists() else {}

    boroughs = gpd.read_file(d / "boundaries" / "nyc_boroughs.gpkg", layer="boroughs")
    fac = gpd.read_parquet(d / "facilities" / "critical_facilities.parquet")
    live = fac[~fac["is_duplicate"]]
    manifest = {
        "data_version": settings["data_version"],
        "manifest_created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "OpenStreetMap contributors, (c) OpenStreetMap contributors, ODbL 1.0 (https://www.openstreetmap.org/copyright)",
        "acquisition_method": {
            "library": f"OSMnx {report.get('osmnx_version', '?')} (Overpass API + Nominatim)",
            "boundaries": "ox.geocode_to_gdf by OSM relation id",
            "facilities": "ox.features_from_polygon(facility_search_area, tags=<config/facility_categories.yaml>)",
            "roads": "ox.graph_from_polygon(road_area, network_type='drive', simplify=True, retain_all=False)",
            "facilities_overpass_endpoint": report.get("facilities", {}).get("overpass_endpoint_used"),
            "roads_overpass_endpoint": report.get("roads", {}).get("overpass_endpoint_used"),
        },
        "acquired_at": {
            "boundaries": str(boroughs["acquired_at"].iloc[0]) if "acquired_at" in boroughs else None,
            "facilities": report.get("facilities", {}).get("acquired_at"),
            "roads": report.get("roads", {}).get("acquired_at"),
        },
        "crs": {"storage": settings["storage_crs"], "metric": settings["metric_crs"]},
        "borough_coverage": [
            {"borough": r.borough, "osm_relation": int(r.osm_id), "area_km2_incl_water": round(float(r.area_km2), 1),
             "display_name": r.display_name} for r in boroughs.itertuples()],
        "boroughs_present": sorted(boroughs["borough"].tolist()),
        "buffers_m": {"facility_search_area": settings["facility_buffer_m"], "road_area": settings["roads"]["buffer_m"]},
        "facilities": {
            "total_features": int(len(fac)), "duplicates_flagged": int(fac["is_duplicate"].sum()),
            "unique_facilities": int(len(live)),
            "by_category": live["category"].value_counts().sort_index().to_dict(),
            "by_category_status": live.groupby("category")["category_status"].first().to_dict(),
            "geometry_types": fac.groupby(["category", "geometry_type"]).size().unstack(fill_value=0).to_dict("index"),
            "outside_nyc_in_buffer": int((live["borough"] == "OUTSIDE_NYC").sum()),
            "unclassified_features_dropped": report.get("facilities", {}).get("unclassified"),
            "repaired_geometries": int(fac["geometry_repaired"].sum()),
            "duplicate_rules": report.get("facilities", {}).get("duplicates"),
        },
        "roads": road_summary(d),
        "files": file_entries(d),
    }
    (d / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str), "utf-8")
    print(f"manifest written: {d / 'manifest.json'} ({len(manifest['files'])} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
