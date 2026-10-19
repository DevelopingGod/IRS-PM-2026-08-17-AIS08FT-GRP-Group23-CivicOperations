"""Stable public entry points for the CivicOps pipeline (see research/integration_contract.md)."""
from __future__ import annotations

import functools
from datetime import datetime

from .config import load_config
from .engine import PlanResult, optimize_plan
from .outputs import build_plan_document, route_geojson
from .schemas import Crew, CrewStatus, Snapshot


@functools.lru_cache(maxsize=2)
def _runtime(config_path: str | None):
    from .runtime import load_runtime
    cfg = load_config(config_path)
    router, catalog, _, _ = load_runtime(cfg)
    return cfg, router, catalog


def propose_plan(c11_output: list, crews: list, now: datetime, *, crew_status: dict | None = None,
                 previous_assignment: dict | None = None, previous_routes: dict | None = None, job_states: dict | None = None,
                 seed: int = 0, method: str = "ga", config_path: str | None = None, run_id: str = "adhoc",
                 version: int = 1) -> dict:
    """One-shot proposal from a C11 queue: returns the versioned plan document (dict).

    ``c11_output``: list returned by ``prioritize_from_c10`` (records preserved as given).
    ``crews``: list of crew dicts (crew_id, agency, skills, depot_lat/lon, work_time_budget_min) or Crew objects.
    The document is a PROPOSAL; ``authorization.dispatch_authorized`` is always False.
    """
    cfg, router, catalog = _runtime(config_path)
    crew_objs = [c if isinstance(c, Crew) else Crew.from_record(c, cfg["capacity"]["work_time_budget_min"]) for c in crews]
    snap = Snapshot(now=now, queue=c11_output, crews=crew_objs, crew_status=crew_status or {},
                    job_states=job_states or {}, previous_assignment=previous_assignment or {},
                    previous_routes=previous_routes or {})
    plan: PlanResult = optimize_plan(snap, router, cfg, catalog, seed=seed, method=method)
    doc = build_plan_document(plan, snap, version=version, run_id=run_id, event={"type": "API_CALL", "time": now},
                              cfg=cfg, data_versions={"road_network": router.net.meta}, c11_info={"provider": "caller-supplied C11 output"},
                              prev_doc=None, job_states=snap.job_states, crews={c.crew_id: c for c in crew_objs},
                              geometry_file=None)
    doc["route_geometry"] = route_geojson(plan, router, version)
    return doc


__all__ = ["propose_plan", "optimize_plan", "Snapshot", "Crew", "CrewStatus", "load_config"]
