"""Shared runtime loading: network/router, catalog, C11 provider, rosters, replay episodes."""
from __future__ import annotations

import functools
from datetime import timedelta
from pathlib import Path

import pandas as pd

from .catalog import load_catalog
from .config import COMPONENT_ROOT, resolve
from .roadnet.network import RoadNetwork, sha256_file
from .roadnet.router import Router
from .schemas import Crew
from .upstream.c11_provider import get_provider

ROSTERS = {"cf0.6": "crews_v2_cf0.6.parquet", "cf0.8": "crews_v2_cf0.8.parquet", "cf1.0": "crews_v2_cf1.0.parquet",
           "cf1.2": "crews_v2_cf1.2.parquet", "legacy": "crews_legacy_v1_budget.parquet"}


@functools.lru_cache(maxsize=4)
def load_network(path: str | None = None) -> RoadNetwork:
    return RoadNetwork.load(resolve(path or "data/road_network/derived/nyc_drive_enriched.npz"))


def make_router(cfg: dict, net: RoadNetwork | None = None) -> Router:
    net = net or load_network(cfg["paths"]["network"])
    r = Router(net, speed_factor=float(cfg["routing"]["speed_factor"]), max_snap_m=float(cfg["routing"]["max_snap_m"]))
    if cfg["routing"].get("heuristic", "alt") == "alt":
        r.build_landmarks(k=int(cfg["routing"].get("landmarks", 8)))
    return r


def load_runtime(cfg: dict):
    catalog = load_catalog(resolve(cfg["paths"]["catalog"]))
    router = make_router(cfg)
    provider, log = get_provider(cfg, catalog)
    return router, catalog, provider, log


def load_roster(name: str, borough: str | None = None, default_budget: float = 420.0) -> list:
    path = COMPONENT_ROOT / "data" / "crews" / ROSTERS.get(name, name)
    df = pd.read_parquet(path)
    if borough is not None and "borough" in df:
        df = df[df["borough"] == borough]
    return [Crew.from_record({**r, "skills": list(r["skills"])}, default_budget) for r in df.to_dict("records")]


@functools.lru_cache(maxsize=1)
def _windows() -> pd.DataFrame:
    return pd.read_parquet(COMPONENT_ROOT / "data" / "replay" / "requests_windows.parquet")


@functools.lru_cache(maxsize=1)
def episodes() -> pd.DataFrame:
    return pd.read_parquet(COMPONENT_ROOT / "data" / "replay" / "episodes.parquet")


def episode_requests(episode_id: str) -> list:
    """Raw (pre-C11) request records of one replay episode, created_date as tz-aware datetime."""
    df = _windows()
    sub = df[df["episode_id"] == episode_id].sort_values(["created_date", "request_id"])
    out = []
    for r in sub.to_dict("records"):
        r["created_date"] = r["created_date"].to_pydatetime()
        for k in ("latitude", "longitude"):
            if pd.isna(r[k]):
                r[k] = None
        r["est_service_minutes"] = float(r["est_service_minutes"])
        out.append(r)
    return out


def episode_info(episode_id: str) -> dict:
    e = episodes()
    row = e[e["episode_id"] == episode_id].iloc[0].to_dict()
    row["t0"] = row["t0"].to_pydatetime()
    row["t1"] = row["t1"].to_pydatetime()
    return row


def static_planning_time(episode_id: str):
    """Static experiments plan once, one minute after the episode's last arrival."""
    return episode_info(episode_id)["t1"] + timedelta(minutes=1)


def data_versions(cfg: dict, roster: str | None = None) -> dict:
    net = load_network(cfg["paths"]["network"])
    out = {"road_network": net.meta, "catalog_sha256": sha256_file(resolve(cfg["paths"]["catalog"]))}
    if roster:
        p = COMPONENT_ROOT / "data" / "crews" / ROSTERS.get(roster, roster)
        out["crew_roster"] = {"name": roster, "file": p.name, "sha256": sha256_file(p)}
    rw = COMPONENT_ROOT / "data" / "replay" / "requests_windows.parquet"
    if rw.exists():
        out["replay_requests_sha256"] = sha256_file(rw)
    return out
