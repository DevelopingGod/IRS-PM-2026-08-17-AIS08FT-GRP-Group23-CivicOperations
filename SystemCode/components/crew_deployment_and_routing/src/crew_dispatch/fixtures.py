"""Small deterministic synthetic fixtures (no NYC data needed). Used by tests and examples.

grid_network(): an R x C grid near (40.70, -73.95) with ~200 m spacing, two-way streets except
row 0 which is one-way eastbound, plus a 2-node island that is NOT strongly connected to the grid.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np

from .roadnet.network import RoadNetwork

TZ = ZoneInfo("America/New_York")
LAT0, LON0 = 40.70, -73.95
DLAT = 0.0018          # ~200 m
DLON = 0.00237         # ~200 m at 40.7N


def grid_network(rows: int = 8, cols: int = 8, speed_mph: float = 25.0, island: bool = True) -> RoadNetwork:
    lat, lon, u, v = [], [], [], []
    for r in range(rows):
        for c in range(cols):
            lat.append(LAT0 + r * DLAT)
            lon.append(LON0 + c * DLON)

    def nid(r, c):
        return r * cols + c

    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                u.append(nid(r, c)); v.append(nid(r, c + 1))
                if r != 0:                       # row 0 is one-way eastbound
                    u.append(nid(r, c + 1)); v.append(nid(r, c))
            if r + 1 < rows:
                u.append(nid(r, c)); v.append(nid(r + 1, c))
                u.append(nid(r + 1, c)); v.append(nid(r, c))
    if island:
        base = len(lat)
        lat += [LAT0 - 0.02, LAT0 - 0.02]
        lon += [LON0 - 0.02, LON0 - 0.0176]
        u += [base, base + 1]
        v += [base + 1, base]
    return RoadNetwork.from_arrays(lat, lon, u, v, edge_speed_mph=np.full(len(u), speed_mph))


def node_latlon(net: RoadNetwork, r: int, c: int, cols: int = 8):
    i = r * cols + c
    return float(net.node_lat[i]), float(net.node_lon[i])


def t(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 10, day, hour, minute, tzinfo=TZ)


def c11_record(rid, agency, skill, lat, lon, pos, status="provisional", service=30, **extra) -> dict:
    """A C11-output-shaped record (fields as returned by prioritize_from_c10)."""
    rec = {"request_id": rid, "agency": agency, "required_skill": skill, "latitude": lat, "longitude": lon,
           "est_service_minutes": service, "c11_queue_status": status,
           "overall_queue_position": None if status == "hold" else pos,
           "dispatch_approved": False, "dispatch_authorization": "not_authorized_by_C11",
           # present in real C11 output but forbidden as Dispatch inputs:
           "priority_band": "PLANNED", "urgency_level": "MEDIUM", "priority_score": 50.0,
           "queue_position": f"M{pos}" if pos else None, "created_date": (t(8) - timedelta(hours=2)).isoformat()}
    rec.update(extra)
    return rec


def crew_record(cid, agency, skills, lat, lon, budget=420) -> dict:
    return {"crew_id": cid, "agency": agency, "skills": list(skills), "depot_lat": lat, "depot_lon": lon,
            "work_time_budget_min": budget}
