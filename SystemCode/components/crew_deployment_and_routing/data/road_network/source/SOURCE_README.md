# CivicOps AI: Shared NYC Road Network (for Crew Dispatch and Routing)

A drivable road graph covering all five NYC boroughs plus a 1 km buffer. It is prepared for the
**Crew Dispatch and Routing** component and has no routing logic. It is not used by External Factors.
Data © OpenStreetMap contributors, ODbL 1.0. Attribute OSM in any map or derivative.

## Contents

```
civicops_shared_osm_roads/
├── data/osm/
│   ├── roads/nyc_drive.graphml      OSMnx MultiDiGraph (directed; load with osmnx.load_graphml)
│   ├── roads/nyc_drive.gpkg         same graph as GIS layers "nodes" and "edges" (QGIS / GeoPandas)
│   ├── boundaries/                  the five borough polygons + coverage buffers it was built from
│   ├── roads_manifest.json          counts, checks, attribute coverage, CRS, sizes, SHA-256
│   └── acquisition_report.json      acquisition settings, endpoint, timing
├── build_roads_manifest.py          re-validates the graph and rewrites roads_manifest.json
└── README.md
```

## The graph (acquired 2026-10-17 via OSMnx 2.1.1 / Overpass)

| Property | Value |
|---|---|
| Nodes / directed edges | 59,251 / 149,355 |
| Total edge length | 17,402 km |
| CRS | EPSG:4326 (lat/lon). Project with `ox.project_graph(G, to_crs="EPSG:32118")` for metres |
| Largest strongly connected component | 58,710 nodes (99.1%). The other 541 nodes cannot reach every other node along one-way edges. Snap routing origins and destinations to the largest component |
| One-way edges | 30.9% |
| Nodes per borough | Queens 21,504, Brooklyn 12,219, Staten Island 9,210, Bronx 7,728, Manhattan 4,643. The other 3,947 nodes lie in the buffer outside NYC |
| Build settings | `network_type="drive"`, `simplify=True`, `retain_all=False` (largest weakly connected component), `truncate_by_edge=True` |

Validation (`build_roads_manifest.py`): directed multigraph ✔, GraphML and GeoPackage node/edge counts match ✔, every edge has
`length` and geometry ✔, weakly connected ✔, all five boroughs present ✔.

**Edge attribute coverage:** `highway` 100%, `oneway`/`reversed` 100%, `name` 97.4%, **`lanes` 32.5%, `maxspeed` 29.4%**,
`ref` 2.5%, `bridge` 1.5%, `access` 0.4%, `tunnel` 0.2%, `junction` 0.2%.

**Not included, so enrich in Crew Dispatch:** speeds and travel times (`maxspeed` is sparse), turn restrictions,
live traffic. Typical enrichment:
```python
import osmnx as ox
G = ox.load_graphml("data/osm/roads/nyc_drive.graphml")
G = ox.routing.add_edge_speeds(G)        # imputes km/h per highway type where maxspeed is missing
G = ox.routing.add_edge_travel_times(G)  # adds travel_time (s)
route = ox.routing.shortest_path(G, orig_node, dest_node, weight="travel_time")
```
Snap crew depots and requests to the graph with `ox.distance.nearest_nodes(G, X=lons, Y=lats)`
(install `scikit-learn` for unprojected graphs, or project the graph first).

## Requirements

Python ≥ 3.11 with `osmnx` (≥2.0), `networkx`, `geopandas`. The pinned set in the External Factors package's
`requirements.txt` works: osmnx 2.1.1, networkx 3.7, geopandas 1.2.0.

## Provenance

Built by `civicops_external_factors/osm_data_prep/download_osm.py --only roads --data-dir <this folder>/data/osm` with the
same borough relations, buffers and `data_version` (`nyc-osm-2026-10-15`) as the External Factors facility cache,
so both components share one geographic baseline. Public Overpass servers were overloaded during acquisition
(15–17 Oct 2026). The download used retries and a response cache. The final run used the maps.mail.ru endpoint.
