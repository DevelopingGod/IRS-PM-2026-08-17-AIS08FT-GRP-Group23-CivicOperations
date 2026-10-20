# Shared OSM Data Package (CivicOps AI)

Data version **`nyc-osm-2026-10-15`**. Source: © OpenStreetMap contributors, licensed under
**ODbL 1.0** (https://www.openstreetmap.org/copyright). Attribute OSM in any map or derivative.
Acquired 2026-10-15 with OSMnx 2.1.1 (Overpass API + Nominatim). Full details, counts, file sizes and SHA-256
checksums are in `data/osm/manifest.json`.

## Layout

```
data/osm/
├── manifest.json                      data version, sources, counts, CRS, sizes, checksums
├── acquisition_report.json            raw counts, timings, endpoints used, unclassified examples
├── boundaries/
│   ├── nyc_boroughs.gpkg              layer "boroughs": 5 OSM admin relations (include water)
│   └── nyc_coverage.gpkg              layers "nyc_union", "facility_search_area" (+2,000 m), "road_area" (+1,000 m)
├── facilities/
│   ├── critical_facilities.gpkg       layer "facilities" (EPSG:4326, original point/polygon geometry)
│   └── critical_facilities.parquet    same table as GeoParquet (runtime loads this)
└── roads/                             (shared road bundle, for Crew Dispatch and Routing)
    ├── nyc_drive.graphml              OSMnx MultiDiGraph
    └── nyc_drive.gpkg                 layers "nodes", "edges"
```
**Runtime (External Factors) needs only** `boundaries/`, `facilities/` and `manifest.json` (≈1.5 MB). These ship
inside the component ZIP, so it is offline-ready. `roads/` is distributed as a separate bundle. Unpack it into the
same `data/osm/` folder, and nothing else needs configuring.

## Boroughs (OSM relations)

| Borough | Relation | Area incl. water (km²) |
|---|---|---|
| MANHATTAN | 8398124 | 86.8 |
| BROOKLYN | 9691750 | 252.3 |
| QUEENS | 9691819 | 465.3 |
| BRONX | 9691916 | 149.0 |
| STATEN ISLAND | 9691948 | 265.6 |

Borough names follow the shared data reference (capitals). The boundaries include water areas and Marble Hill (Manhattan).

## Facility table schema

`facility_id` (`node/123`, `way/456`, `relation/789`), `osm_type`, `osm_id`, `category`, `category_status`
(`core` / `exploratory`), `name`, `geometry_type`, `borough` (or `OUTSIDE_NYC`), `is_duplicate`, `duplicate_of`,
`geometry_repaired`, `tags_json` (all OSM tags), `acquired_at`, selected `tag_*` columns, `geometry`.
Categories and tags: `config/facility_categories.yaml`.

| Category | Status | Unique | Points | Polygons |
|---|---|---|---|---|
| HOSPITAL | core | 97 | 21 | 77 |
| SCHOOL | core | 2,555 | 1,194 | 1,479 |
| CHILDCARE | core | 482 | 422 | 61 |
| SENIOR_CARE | core | 170 | 79 | 94 |
| FIRE_STATION | core | 348 | 171 | 180 |
| POLICE | core | 151 | 76 | 76 |
| TRANSIT_STATION | exploratory | 642 | 619 | 31 |

Point and polygon columns count all features, including the 135 flagged duplicates. "Unique" excludes duplicates.
404 unique facilities lie outside NYC, inside the 2 km buffer.

## Road network (for Crew Dispatch and Routing)

**Status in this build: PENDING.** All public Overpass servers were overloaded or down during acquisition on
2026-10-15/16 (HTTP 502/504/500, timeouts). 8 of the 18 road sub-queries completed and sit in the local HTTP cache,
so the download resumes where it stopped. External Factors does **not** use the road graph. It is prepared only for
the future Crew Dispatch and Routing component.

To complete it (internet required, one-off):
```bash
python osm_data_prep/download_osm.py --only roads
```
```bash
python osm_data_prep/build_manifest.py
```
```bash
python osm_data_prep/validate_osm.py
```
```bash
python tools/make_dist.py
```
This writes `roads/nyc_drive.graphml` and `roads/nyc_drive.gpkg` and adds the road section (nodes, edges, attribute
coverage) to `manifest.json`. `make_dist.py` then emits `civicops_osm_roads_<data_version>.zip`.

Specification (`config/osm_data.yaml`): `network_type="drive"`, borough union + 1,000 m buffer (keeps bridge
approaches and cross-borough links), simplified topology, largest weakly connected component retained, directed
MultiDiGraph with `oneway`/`reversed`, `length` (m), edge geometry, and the OSM attributes `highway`, `name`,
`lanes`, `maxspeed`, `bridge`, `tunnel`, `ref`, `access` where present. No speeds or travel times are imputed.
OSM `maxspeed`/`lanes` are usually sparse, so Crew Dispatch should enrich them (for example with
`osmnx.routing.add_edge_speeds` / `add_edge_travel_times` and agreed defaults). Load with:
```python
import osmnx as ox
G = ox.load_graphml("data/osm/roads/nyc_drive.graphml")
```

## Commands

```bash
python osm_data_prep/download_osm.py
```
```bash
python osm_data_prep/download_osm.py --refresh --only facilities
```
```bash
python osm_data_prep/build_manifest.py
```
```bash
python osm_data_prep/validate_osm.py --json outputs/tables/osm_validation.json
```
The downloader reuses existing outputs (no repeat downloads) and an HTTP cache (`data/osm/_http_cache/`, not
distributed). It retries overloaded Overpass servers with exponential backoff across the endpoints in
`config/osm_data.yaml`. After any refresh, rebuild the manifest and bump `data_version`, because evaluations record it.
