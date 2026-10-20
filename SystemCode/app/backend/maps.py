"""Streets for the zoomed maps, from the NYC drive network in Mohammad's Crew deployment package.

The network holds about 150,000 road segments with their real shapes (OpenStreetMap). For a map
window it returns the segments inside it, simplified, with a road class so the portals can draw
main roads bolder than side streets. Everything is local: no map service is called.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np

NETWORK = (Path(__file__).resolve().parents[2] / "components" / "crew_deployment_and_routing" / "data"
           / "road_network" / "derived" / "nyc_drive_enriched.npz")
MAJOR = {"motorway", "trunk", "motorway_link", "trunk_link"}
MAIN = {"primary", "secondary", "primary_link", "secondary_link"}


@lru_cache(maxsize=1)
def _net() -> dict:
    z = np.load(NETWORK, allow_pickle=False)
    u, v = z["edge_u"], z["edge_v"]
    keep = u < v                     # each two way street is stored twice: draw it once
    hw = z["edge_highway"]
    cls = np.where(np.isin(hw, list(MAJOR)), 2, np.where(np.isin(hw, list(MAIN)), 1, 0)).astype(np.int8)
    return {"lat": z["node_lat"][u], "lon": z["node_lon"][u], "lat2": z["node_lat"][v], "lon2": z["node_lon"][v],
            "keep": keep, "cls": cls, "off": z["geom_offsets"], "xy": z["geom_xy"]}


def streets(south: float, west: float, north: float, east: float, limit: int = 7000) -> list[dict]:
    """Road segments that touch the window, main roads first so a crowded window keeps them."""
    n = _net()
    inside = (((n["lat"] >= south) & (n["lat"] <= north) & (n["lon"] >= west) & (n["lon"] <= east))
              | ((n["lat2"] >= south) & (n["lat2"] <= north) & (n["lon2"] >= west) & (n["lon2"] <= east)))
    idx = np.flatnonzero(inside & n["keep"])
    idx = idx[np.argsort(-n["cls"][idx], kind="stable")][:limit]
    out = []
    for i in idx:
        pts = n["xy"][n["off"][i]:n["off"][i + 1]]
        if len(pts) > 4:             # keep the ends and every other bend, enough at street scale
            pts = np.vstack([pts[:-1:2], pts[-1:]])
        out.append({"k": int(n["cls"][i]), "p": np.round(pts, 5).tolist()})
    return out
