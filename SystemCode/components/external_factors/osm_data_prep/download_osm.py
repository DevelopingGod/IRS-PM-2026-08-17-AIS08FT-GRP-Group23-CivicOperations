"""Download and cache the OSM data needed by CivicOps AI.

Produces, under --data-dir (default data/osm):
  boundaries/nyc_boroughs.gpkg          five borough polygons (OSM admin relations)
  boundaries/nyc_coverage.gpkg          layers: nyc_union, facility_search_area, road_area
  facilities/critical_facilities.gpkg   normalised facilities (original geometry, EPSG:4326)
  facilities/critical_facilities.parquet same table as GeoParquet (fast runtime loading)
  roads/nyc_drive.graphml               drivable road graph (for future Crew Dispatch)
  roads/nyc_drive.gpkg                  layers: nodes, edges (same graph as GIS layers)
  acquisition_report.json               counts, timings, unclassified examples

Existing outputs are reused unless --refresh is given, so repeated runs do not re-download.
Internet access is needed only here, never during evaluation.

Usage:
  python osm_data_prep/download_osm.py                    # everything missing
  python osm_data_prep/download_osm.py --only facilities  # one stage
  python osm_data_prep/download_osm.py --refresh          # force re-download of all stages
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import geopandas as gpd  # noqa: E402
import osmnx as ox  # noqa: E402
import pandas as pd  # noqa: E402
import yaml  # noqa: E402

from civicops_external_factors.facility_catalog import (  # noqa: E402
    CategoryCatalog, assign_boroughs, mark_duplicates, normalise_osm_features)

STAGES = ("boundaries", "facilities", "roads")


def log(msg: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


def load_settings(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def configure_osmnx(settings: dict, data_dir: Path) -> None:
    ox.settings.use_cache = True
    ox.settings.cache_folder = str(data_dir / "_http_cache")
    ox.settings.log_console = False
    ox.settings.requests_timeout = int(settings["overpass"]["requests_timeout_s"])
    if settings["overpass"].get("max_query_area_m2"):
        ox.settings.max_query_area_size = float(settings["overpass"]["max_query_area_m2"])


def with_overpass_retry(settings: dict, func, *args, **kwargs):
    """Call an OSMnx Overpass function, retrying with exponential backoff across endpoints."""
    ov = settings["overpass"]
    last_exc: Exception | None = None
    for endpoint in ov.get("endpoints", [ox.settings.overpass_url]):
        ox.settings.overpass_url = endpoint
        wait = float(ov.get("initial_backoff_s", 30))
        for attempt in range(1, int(ov.get("max_attempts", 3)) + 1):
            try:
                log(f"  overpass endpoint {endpoint} (attempt {attempt})")
                return func(*args, **kwargs)
            except Exception as exc:  # network / server-capacity errors
                last_exc = exc
                first_line = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
                log(f"  failed: {first_line[:160]}; retrying in {wait:.0f}s")
                time.sleep(wait)
                wait *= 2
    raise RuntimeError(f"All Overpass endpoints failed: {last_exc}") from last_exc


def stage_boundaries(settings: dict, data_dir: Path, report: dict) -> gpd.GeoDataFrame:
    out = data_dir / "boundaries" / "nyc_boroughs.gpkg"
    cov = data_dir / "boundaries" / "nyc_coverage.gpkg"
    metric = settings["metric_crs"]
    rows = []
    for borough, spec in settings["boroughs"].items():
        log(f"boundary: {borough} (OSM relation {spec['osm_relation']})")
        g = ox.geocode_to_gdf(f"R{spec['osm_relation']}", by_osmid=True)
        r = g.iloc[0]
        rows.append({"borough": borough, "osm_type": "relation", "osm_id": int(spec["osm_relation"]),
                     "display_name": r.get("display_name"), "geometry": g.geometry.iloc[0]})
    b = gpd.GeoDataFrame(rows, geometry="geometry", crs="EPSG:4326")
    b["area_km2"] = b.to_crs(metric).area / 1e6
    b["acquired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    out.parent.mkdir(parents=True, exist_ok=True)
    b.to_file(out, layer="boroughs", driver="GPKG")

    union_m = b.to_crs(metric).geometry.union_all()
    layers = {
        "nyc_union": union_m,
        "facility_search_area": union_m.buffer(settings["facility_buffer_m"]),
        "road_area": union_m.buffer(settings["roads"]["buffer_m"]),
    }
    for name, geom in layers.items():
        gdf = gpd.GeoDataFrame({"layer": [name],
                                "buffer_m": [{"nyc_union": 0,
                                              "facility_search_area": settings["facility_buffer_m"],
                                              "road_area": settings["roads"]["buffer_m"]}[name]]},
                               geometry=[geom], crs=metric).to_crs("EPSG:4326")
        gdf.to_file(cov, layer=name, driver="GPKG")
    report["boundaries"] = {"boroughs": b[["borough", "osm_id", "display_name", "area_km2"]]
                            .to_dict("records")}
    return b


def read_layer(path: Path, layer: str) -> gpd.GeoDataFrame:
    return gpd.read_file(path, layer=layer)


def stage_facilities(settings: dict, data_dir: Path, report: dict) -> None:
    catalog = CategoryCatalog.from_yaml(ROOT / "config" / "facility_categories.yaml")
    metric = settings["metric_crs"]
    area = read_layer(data_dir / "boundaries" / "nyc_coverage.gpkg", "facility_search_area")
    boroughs = read_layer(data_dir / "boundaries" / "nyc_boroughs.gpkg", "boroughs")
    tags = catalog.combined_query_tags()
    log(f"facilities: querying Overpass with tags {tags}")
    t0 = time.time()
    raw = with_overpass_retry(settings, ox.features_from_polygon, area.geometry.iloc[0], tags=tags)
    endpoint_used = ox.settings.overpass_url
    log(f"facilities: {len(raw)} raw features in {time.time() - t0:.0f}s")
    acquired_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    fac, norm_report = normalise_osm_features(raw, catalog, acquired_at)
    fac, dup_report = mark_duplicates(fac, metric)
    fac["borough"] = assign_boroughs(fac, boroughs, metric).values
    outdir = data_dir / "facilities"
    outdir.mkdir(parents=True, exist_ok=True)
    fac.to_file(outdir / "critical_facilities.gpkg", layer="facilities", driver="GPKG")
    fac.to_parquet(outdir / "critical_facilities.parquet", index=False)
    report["facilities"] = {"query_tags": tags, "overpass_endpoint_used": endpoint_used, "download_seconds": round(time.time() - t0, 1),
                            "acquired_at": acquired_at, **norm_report, "duplicates": dup_report,
                            "category_catalog_version": catalog.version}
    log(f"facilities: saved {len(fac)} facilities ({dup_report['total_duplicates']} duplicates flagged)")


def stage_roads(settings: dict, data_dir: Path, report: dict) -> None:
    rs = settings["roads"]
    area = read_layer(data_dir / "boundaries" / "nyc_coverage.gpkg", "road_area")
    log("roads: downloading drivable network (this can take several minutes)")
    t0 = time.time()
    G = with_overpass_retry(settings, ox.graph_from_polygon, area.geometry.iloc[0],
                            network_type=rs["network_type"], simplify=rs["simplify"],
                            retain_all=rs["retain_all"], truncate_by_edge=rs["truncate_by_edge"])
    endpoint_used = ox.settings.overpass_url
    G.graph["civicops_data_version"] = settings["data_version"]
    G.graph["civicops_acquired_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    G.graph["civicops_note"] = ("Raw OSM drive network for future Crew Dispatch and Routing. "
                                "No speeds or travel times imputed.")
    outdir = data_dir / "roads"
    outdir.mkdir(parents=True, exist_ok=True)
    ox.save_graphml(G, outdir / "nyc_drive.graphml")
    ox.save_graph_geopackage(G, outdir / "nyc_drive.gpkg", directed=True)
    report["roads"] = {"overpass_endpoint_used": endpoint_used, "acquired_at": G.graph["civicops_acquired_at"], "nodes": G.number_of_nodes(), "edges": G.number_of_edges(),
                       "download_seconds": round(time.time() - t0, 1), **{k: v for k, v in rs.items()}}
    log(f"roads: saved graph with {G.number_of_nodes()} nodes / {G.number_of_edges()} edges")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data-dir", default=str(ROOT / "data" / "osm"))
    ap.add_argument("--settings", default=str(ROOT / "config" / "osm_data.yaml"))
    ap.add_argument("--only", choices=STAGES, action="append", help="run only these stages")
    ap.add_argument("--refresh", action="store_true", help="re-download even if outputs exist")
    args = ap.parse_args(argv)

    data_dir = Path(args.data_dir)
    settings = load_settings(Path(args.settings))
    configure_osmnx(settings, data_dir)
    report_path = data_dir / "acquisition_report.json"
    report = json.loads(report_path.read_text("utf-8")) if report_path.exists() else {}
    report["data_version"] = settings["data_version"]
    report["osmnx_version"] = ox.__version__
    report["overpass_endpoint"] = ox.settings.overpass_url

    outputs = {
        "boundaries": [data_dir / "boundaries" / "nyc_boroughs.gpkg", data_dir / "boundaries" / "nyc_coverage.gpkg"],
        "facilities": [data_dir / "facilities" / "critical_facilities.parquet"],
        "roads": [data_dir / "roads" / "nyc_drive.graphml"],
    }
    funcs = {"boundaries": stage_boundaries, "facilities": stage_facilities, "roads": stage_roads}
    for stage in args.only or STAGES:
        if not args.refresh and all(p.exists() for p in outputs[stage]):
            log(f"{stage}: cached outputs found, skipping (use --refresh to re-download)")
            continue
        funcs[stage](settings, data_dir, report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2, default=str), "utf-8")
    log("done. Next: python osm_data_prep/build_manifest.py && python osm_data_prep/validate_osm.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
