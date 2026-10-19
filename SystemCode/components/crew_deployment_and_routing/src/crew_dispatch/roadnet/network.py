"""Compact array representation of the directed drive network.

``RoadNetwork`` stores nodes, every directed OSM edge (with geometry and speed provenance) and
nothing else. The enriched copy is saved as ``data/road_network/derived/nyc_drive_enriched.npz``
so that runtime code never needs osmnx/geopandas or the 80 MB GraphML.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from . import speeds as spd

EARTH_RADIUS_M = 6371008.8  # same mean radius OSMnx uses for edge lengths / great-circle distances


def haversine_m(lat1, lon1, lat2, lon2):
    """Great-circle distance in metres (numpy-broadcasting)."""
    lat1, lon1, lat2, lon2 = (np.radians(np.asarray(x, dtype=float)) for x in (lat1, lon1, lat2, lon2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lon2 - lon1) / 2) ** 2
    return 2 * EARTH_RADIUS_M * np.arcsin(np.sqrt(np.clip(a, 0.0, 1.0)))


@dataclass
class RoadNetwork:
    node_osmid: np.ndarray          # (N,) str
    node_lat: np.ndarray            # (N,) float64
    node_lon: np.ndarray            # (N,) float64
    edge_u: np.ndarray              # (E,) int32 node index
    edge_v: np.ndarray              # (E,) int32 node index
    edge_length_m: np.ndarray       # (E,) float64
    edge_speed_mph: np.ndarray      # (E,) float64 posted/legal speed estimate
    edge_speed_source: np.ndarray   # (E,) int8 index into SPEED_SOURCES
    edge_highway: np.ndarray        # (E,) str (first class)
    edge_inside_nyc: np.ndarray     # (E,) bool
    geom_offsets: np.ndarray        # (E+1,) int64 offsets into geom_xy
    geom_xy: np.ndarray             # (P, 2) float64 lon, lat
    meta: dict = field(default_factory=dict)

    @property
    def n_nodes(self) -> int:
        return len(self.node_lat)

    @property
    def n_edges(self) -> int:
        return len(self.edge_u)

    def edge_geometry(self, e: int) -> np.ndarray:
        return self.geom_xy[self.geom_offsets[e]:self.geom_offsets[e + 1]]

    def speed_source_labels(self) -> np.ndarray:
        return np.asarray(spd.SPEED_SOURCES)[self.edge_speed_source]

    # ------------------------------------------------------------------ persistence
    def save(self, path: Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(
            path, node_osmid=self.node_osmid.astype("U"), node_lat=self.node_lat, node_lon=self.node_lon,
            edge_u=self.edge_u, edge_v=self.edge_v, edge_length_m=self.edge_length_m,
            edge_speed_mph=self.edge_speed_mph, edge_speed_source=self.edge_speed_source,
            edge_highway=self.edge_highway.astype("U"), edge_inside_nyc=self.edge_inside_nyc,
            geom_offsets=self.geom_offsets, geom_xy=self.geom_xy,
            meta=np.asarray(json.dumps(self.meta)))

    @classmethod
    def load(cls, path: Path) -> "RoadNetwork":
        z = np.load(Path(path), allow_pickle=False)
        return cls(node_osmid=z["node_osmid"], node_lat=z["node_lat"], node_lon=z["node_lon"],
                   edge_u=z["edge_u"], edge_v=z["edge_v"], edge_length_m=z["edge_length_m"],
                   edge_speed_mph=z["edge_speed_mph"], edge_speed_source=z["edge_speed_source"],
                   edge_highway=z["edge_highway"], edge_inside_nyc=z["edge_inside_nyc"],
                   geom_offsets=z["geom_offsets"], geom_xy=z["geom_xy"], meta=json.loads(str(z["meta"])))

    # ------------------------------------------------------------------ construction
    @classmethod
    def from_arrays(cls, node_lat, node_lon, edge_u, edge_v, edge_length_m=None, edge_speed_mph=None,
                    edge_highway=None, node_osmid=None, meta=None) -> "RoadNetwork":
        """Build a network from plain arrays (used by test fixtures). Straight-line geometry."""
        node_lat = np.asarray(node_lat, float)
        node_lon = np.asarray(node_lon, float)
        u = np.asarray(edge_u, np.int32)
        v = np.asarray(edge_v, np.int32)
        if edge_length_m is None:
            edge_length_m = haversine_m(node_lat[u], node_lon[u], node_lat[v], node_lon[v])
        e = len(u)
        speed = np.full(e, 25.0) if edge_speed_mph is None else np.asarray(edge_speed_mph, float)
        hw = np.asarray(["residential"] * e if edge_highway is None else edge_highway, dtype="U")
        xy = np.empty((2 * e, 2))
        xy[0::2, 0], xy[0::2, 1] = node_lon[u], node_lat[u]
        xy[1::2, 0], xy[1::2, 1] = node_lon[v], node_lat[v]
        return cls(node_osmid=np.asarray(node_osmid if node_osmid is not None else [str(i) for i in range(len(node_lat))], dtype="U"),
                   node_lat=node_lat, node_lon=node_lon, edge_u=u, edge_v=v,
                   edge_length_m=np.asarray(edge_length_m, float), edge_speed_mph=speed,
                   edge_speed_source=np.zeros(e, np.int8), edge_highway=hw,
                   edge_inside_nyc=np.ones(e, bool), geom_offsets=np.arange(0, 2 * e + 1, 2, dtype=np.int64),
                   geom_xy=xy, meta=meta or {"data_version": "fixture"})


# ---------------------------------------------------------------------- GraphML -> enriched arrays
def _read_gpkg_polygons(path: Path, table: str):
    import shapely
    con = sqlite3.connect(str(path))
    try:
        blobs = [r[0] for r in con.execute(f'select geom from "{table}"')]
    finally:
        con.close()
    geoms = []
    for b in blobs:
        flags = b[3]
        env = {0: 0, 1: 32, 2: 48, 3: 48, 4: 64}[(flags >> 1) & 0x07]
        geoms.append(shapely.from_wkb(bytes(b[8 + env:])))
    return shapely.union_all(geoms)


def _parse_linestring(wkt: str) -> np.ndarray:
    body = wkt[wkt.index("(") + 1: wkt.rindex(")")]
    return np.array([[float(t) for t in pair.split()] for pair in body.split(",")], dtype=float)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def build_from_graphml(graphml: Path, nyc_union_gpkg: Path, data_version: str = "nyc-osm-2026-10-15"):
    """Parse the copied GraphML, impute speeds, and return (RoadNetwork, enrichment report)."""
    import networkx as nx
    import shapely

    G = nx.read_graphml(str(graphml))
    osmids = list(G.nodes)
    index = {n: i for i, n in enumerate(osmids)}
    lat = np.array([float(G.nodes[n]["y"]) for n in osmids])
    lon = np.array([float(G.nodes[n]["x"]) for n in osmids])

    us, vs, lengths, hws, maxs, geoms = [], [], [], [], [], []
    reversed_geom = 0
    for u, v, d in G.edges(data=True):
        iu, iv = index[u], index[v]
        us.append(iu)
        vs.append(iv)
        lengths.append(float(d["length"]))
        hws.append(d.get("highway"))
        maxs.append(d.get("maxspeed"))
        g = _parse_linestring(d["geometry"]) if d.get("geometry") else np.array([[lon[iu], lat[iu]], [lon[iv], lat[iv]]])
        # Orient geometry from u to v so that concatenated route polylines are continuous.
        if np.hypot(*(g[0] - (lon[iu], lat[iu]))) > np.hypot(*(g[-1] - (lon[iu], lat[iu]))):
            g = g[::-1]
            reversed_geom += 1
        geoms.append(g)
    us = np.array(us, np.int32)
    vs = np.array(vs, np.int32)
    lengths = np.array(lengths)

    nyc = _read_gpkg_polygons(nyc_union_gpkg, "nyc_union")
    mid_lon = (lon[us] + lon[vs]) / 2
    mid_lat = (lat[us] + lat[vs]) / 2
    inside = shapely.contains_xy(nyc, mid_lon, mid_lat)

    mph, sources, report = spd.impute_speeds(hws, maxs, inside)
    src_idx = np.array([spd.SPEED_SOURCES.index(s) for s in sources], np.int8)
    offsets = np.zeros(len(geoms) + 1, np.int64)
    offsets[1:] = np.cumsum([len(g) for g in geoms])

    hav = haversine_m(lat[us], lon[us], lat[vs], lon[vs])
    ratio = np.where(hav > 0, lengths / np.where(hav > 0, hav, 1), np.inf)
    report.update({
        "nodes": len(osmids), "inside_nyc_edges": int(inside.sum()), "outside_nyc_edges": int((~inside).sum()),
        "geometry_reoriented_edges": reversed_geom,
        "length_over_haversine_min": float(ratio[np.isfinite(ratio)].min()),
        "edges_length_below_haversine": int((ratio < 1.0).sum()),
        "posted_mph_by_source": {s: {"edges": int((src_idx == k).sum()),
                                     "mean_mph": round(float(np.mean(np.asarray(mph)[src_idx == k])), 2) if (src_idx == k).any() else None}
                                 for k, s in enumerate(spd.SPEED_SOURCES)},
        "length_km_by_source": {s: round(float(lengths[src_idx == k].sum() / 1000), 1) for k, s in enumerate(spd.SPEED_SOURCES)},
    })
    net = RoadNetwork(node_osmid=np.asarray(osmids, dtype="U"), node_lat=lat, node_lon=lon, edge_u=us, edge_v=vs,
                      edge_length_m=lengths, edge_speed_mph=np.asarray(mph), edge_speed_source=src_idx,
                      edge_highway=np.asarray([spd.highway_class(h) for h in hws], dtype="U"),
                      edge_inside_nyc=inside, geom_offsets=offsets, geom_xy=np.concatenate(geoms),
                      meta={"data_version": data_version, "source_graphml_sha256": sha256_file(graphml),
                            "derived_by": "crew_dispatch.roadnet.network.build_from_graphml"})
    return net, report
