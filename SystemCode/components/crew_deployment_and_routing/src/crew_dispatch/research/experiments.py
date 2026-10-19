"""Reproducible experiments (``python -m crew_dispatch experiment <name>``).

Split discipline: ``tuning`` uses VALIDATION episodes only; everything reported as a result uses TEST
episodes with the configuration frozen after tuning. Workforce rosters were sized on TRAIN episodes.
Each worker process loads the network once; tasks are grouped per episode so the travel-time row
cache can be cleared between episodes.

Outputs: ``outputs/benchmarks/<experiment>.csv`` (+ ``.json`` metadata).
"""
from __future__ import annotations

import itertools
import json
import os
import random
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np
import pandas as pd

from ..config import COMPONENT_ROOT, deep_merge, load_config

OUT = COMPONENT_ROOT / "outputs" / "benchmarks"
G: dict = {}


# ---------------------------------------------------------------------- worker side
def _init(base_cfg: dict):
    import warnings
    warnings.filterwarnings("ignore")
    from ..catalog import load_catalog
    from ..config import resolve
    from ..runtime import load_network, make_router
    from ..upstream.c11_provider import get_provider
    G["cfg"] = base_cfg
    G["catalog"] = load_catalog(resolve(base_cfg["paths"]["catalog"]))
    G["net"] = load_network(base_cfg["paths"]["network"])
    G["routers"] = {}
    G["provider"], _ = get_provider(base_cfg, G["catalog"])


def _router(speed_factor: float):
    from ..runtime import make_router
    key = round(float(speed_factor), 4)
    if key not in G["routers"]:
        cfg = deep_merge(G["cfg"], {"routing": {"speed_factor": key}})
        G["routers"][key] = make_router(cfg, G["net"])
    return G["routers"][key]


def _flat(prefix: str, d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(_flat(f"{prefix}{k}.", v))
        elif isinstance(v, (int, float, str, bool)) or v is None:
            out[f"{prefix}{k}"] = v
    return out


def static_episode_task(task: dict) -> list:
    """All configurations of one episode (static plan at the end of the window)."""
    from ..engine import optimize_plan
    from ..runtime import episode_info, episode_requests, load_roster, static_planning_time
    from ..schemas import Snapshot
    rows = []
    ep = task["episode"]
    try:
        info = episode_info(ep)
        now = static_planning_time(ep)
        queue = G["provider"].prioritize(episode_requests(ep), now)
        for run in task["runs"]:
            cfg = deep_merge(G["cfg"], run.get("overrides", {}))
            router = _router(cfg["routing"]["speed_factor"])
            crews = load_roster(run["roster"], info["borough"])
            t0 = time.perf_counter()
            p = optimize_plan(Snapshot(now=now, queue=queue, crews=crews, crew_status={}), router, cfg, G["catalog"],
                              seed=run.get("seed", 0), method=run["method"], reconstruct_paths=False, keep_instances=True)
            fit = sum(s.fitness() for s in p.solutions.values())
            row = {"episode": ep, "split": info["split"], "borough": info["borough"], "start_hour": info["start_hour"],
                   "daytime": bool(info["daytime"]), "n_requests": int(info["n"]), "roster": run["roster"],
                   "method": run["method"], "seed": run.get("seed", 0), "tag": run.get("tag", ""), "fitness": fit,
                   "wall_s": time.perf_counter() - t0, "crews": len(crews)}
            row.update({k: v for k, v in p.metrics.items() if not isinstance(v, dict)})
            for r, c in p.metrics["unserved_reason_counts"].items():
                row[f"reason.{r}"] = c
            row.update(_flat("p.", run.get("params", {})))
            ga = [m for m in p.per_agency.values() if "generations" in m]
            if ga:
                row["ga_generations_mean"] = float(np.mean([m["generations"] for m in ga]))
                row["ga_time_limit_hits"] = int(sum(m["terminated_by"] == "time_limit" for m in ga))
            rows.append(row)
        for r in G["routers"].values():
            r.clear_cache()
    except Exception as e:  # keep the sweep alive, record the failure
        rows.append({"episode": ep, "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]})
    return rows


def dynamic_task(task: dict) -> dict:
    from ..dynamic.controller import DispatchController
    from ..dynamic.simulator import ReplaySimulator
    from ..runtime import episode_info, episode_requests, load_roster
    ep, pol = task["episode"], task["policy"]
    try:
        cfg = deep_merge(G["cfg"], pol.get("overrides", {}))
        router = _router(cfg["routing"]["speed_factor"])
        info = episode_info(ep)
        crews = load_roster(task["roster"], info["borough"])
        ctl = DispatchController(crews, router, cfg, G["catalog"], COMPONENT_ROOT / "cache" / "dyn_unused", run_id="exp",
                                 seed=task["seed"], method=pol.get("method", "ga"), replan_method=pol.get("replan_method", "ga"),
                                 maps=False, write_outputs=False,
                                 ga_replan_overrides={"generations": cfg["dynamic"]["ga_generations_replan"]})
        sim = ReplaySimulator(ctl, G["provider"], episode_requests(ep), cfg, seed=task["seed"])
        ctl.start_duty(info["t0"])
        end = max(st.duty_end for st in ctl.crew_status.values())
        t0 = time.perf_counter()
        s = sim.run(info["t0"], end)
        s.update({"episode": ep, "policy": pol["name"], "seed": task["seed"], "roster": task["roster"],
                  "wall_s": time.perf_counter() - t0, "borough": info["borough"], "n_requests": int(info["n"])})
        router.clear_cache()
        return s
    except Exception as e:
        return {"episode": ep, "policy": pol["name"], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}


# ---------------------------------------------------------------------- driver side
def _pool_map(fn, tasks, cfg, workers):
    out = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(max_workers=workers, initializer=_init, initargs=(cfg,)) as ex:
        futs = [ex.submit(fn, t) for t in tasks]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            out.extend(r if isinstance(r, list) else [r])
            if i % max(1, len(tasks) // 10) == 0:
                print(f"  {i}/{len(tasks)} tasks done ({time.perf_counter() - t0:.0f}s)", flush=True)
    return out


def _episodes(split: str, daytime: bool | None = None) -> list:
    from ..runtime import episodes
    e = episodes()
    e = e[e["split"] == split]
    if daytime is not None:
        e = e[e["daytime"] == daytime]
    return sorted(e["episode_id"])


def _save(name: str, rows: list, meta: dict):
    OUT.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    errs = df[df.get("error").notna()] if "error" in df else df.iloc[0:0]
    df.to_csv(OUT / f"{name}.csv", index=False)
    meta = {**meta, "rows": int(len(df)), "errors": int(len(errs)), "created": time.strftime("%Y-%m-%dT%H:%M:%S")}
    (OUT / f"{name}.json").write_text(json.dumps(meta, indent=2, default=str))
    print(f"saved {OUT / name}.csv rows={len(df)} errors={len(errs)}")
    if len(errs):
        print(errs[["episode", "error"]].head())
    return df


# ---------------------------------------------------------------------- experiments
TUNING_WEIGHTS = [("reciprocal", 10), ("reciprocal", 25), ("reciprocal", 75), ("exponential", 25), ("exponential", 75),
                  ("linear", 0), ("log", 0)]
TUNING_LAMBDA_B = [60, 120, 240]


def exp_tuning(cfg: dict, workers: int = 20):
    """VALIDATION only. (a) rank-weight transform x lambda_B; (b) GA size. Baselines included for the rule."""
    eps = _episodes("val")
    tasks = []
    for ep in eps:
        runs = [{"roster": "cf0.8", "method": m, "seed": 0, "tag": "baseline"} for m in ("rank_first_greedy", "travel_aware_greedy")]
        for (tr, prm), lb in itertools.product(TUNING_WEIGHTS, TUNING_LAMBDA_B):
            for seed in (0, 1):
                runs.append({"roster": "cf0.8", "method": "ga", "seed": seed, "tag": "weights",
                             "overrides": {"objective": {"rank_weight": {"transform": tr, "param": prm or 25}, "lambda_backlog": lb}},
                             "params": {"transform": tr, "param": prm, "lambda_backlog": lb}})
        for name, ga in {"small": {"population": 24, "generations": 80, "stall_generations": 20},
                         "default": {}, "large": {"population": 60, "generations": 250, "stall_generations": 50}}.items():
            for seed in (0, 1):
                runs.append({"roster": "cf0.8", "method": "ga", "seed": seed, "tag": "ga_size", "overrides": {"ga": ga},
                             "params": {"ga_size": name}})
        tasks.append({"episode": ep, "runs": runs})
    rows = _pool_map(static_episode_task, tasks, cfg, workers)
    return _save("tuning_val", rows, {"split": "val", "episodes": eps, "roster": "cf0.8",
                                      "weights": TUNING_WEIGHTS, "lambda_backlog": TUNING_LAMBDA_B})


def exp_tuning_ext(cfg: dict, workers: int = 20):
    """VALIDATION only - protocol amendment A1: the pre-registered lambda_B grid {60,120,240} peaked at its
    upper boundary for every transform, so the grid is extended to {480, 960, lexicographic} for all transforms."""
    eps = _episodes("val")
    tasks = []
    for ep in eps:
        runs = []
        for (tr, prm) in TUNING_WEIGHTS:
            for lb in (480, 960, "lex"):
                obj = {"rank_weight": {"transform": tr, "param": prm or 25}}
                obj.update({"mode": "lexicographic"} if lb == "lex" else {"lambda_backlog": lb})
                for seed in (0, 1):
                    runs.append({"roster": "cf0.8", "method": "ga", "seed": seed, "tag": "weights", "overrides": {"objective": obj},
                                 "params": {"transform": tr, "param": prm, "lambda_backlog": 1e6 if lb == "lex" else lb}})
        tasks.append({"episode": ep, "runs": runs})
    rows = _pool_map(static_episode_task, tasks, cfg, workers)
    return _save("tuning_val_ext", rows, {"split": "val", "episodes": eps, "amendment": "A1 lambda_B grid extension"})


def exp_static(cfg: dict, workers: int = 20, seeds=(0, 1, 2, 3, 4)):
    """TEST: GA vs baselines under five workforce scenarios (frozen configuration)."""
    eps = _episodes("test")
    rosters = ["cf0.6", "cf0.8", "cf1.0", "cf1.2", "legacy"]
    tasks = []
    for ep in eps:
        runs = []
        for ro in rosters:
            runs += [{"roster": ro, "method": m, "seed": 0} for m in ("rank_first_greedy", "travel_aware_greedy")]
            runs += [{"roster": ro, "method": "ga", "seed": s} for s in seeds]
        tasks.append({"episode": ep, "runs": runs})
    rows = _pool_map(static_episode_task, tasks, cfg, workers)
    return _save("static_test", rows, {"split": "test", "episodes": eps, "rosters": rosters, "ga_seeds": list(seeds),
                                       "config": cfg})


def exp_speed(cfg: dict, workers: int = 20):
    """TEST subset: sensitivity of plans to the posted->travel speed factor (A-SPD-4)."""
    eps = _episodes("test")[::3]
    tasks = [{"episode": ep, "runs": [{"roster": "cf0.8", "method": m, "seed": 0, "overrides": {"routing": {"speed_factor": f}},
                                       "params": {"speed_factor": f}}
                                      for f in (0.5, 0.6, 0.75, 1.0) for m in ("ga", "rank_first_greedy")]} for ep in eps]
    rows = _pool_map(static_episode_task, tasks, cfg, workers)
    return _save("speed_sensitivity_test", rows, {"split": "test", "episodes": eps})


def exp_budget(cfg: dict, workers: int = 20):
    """TEST subset: productive work-time budget scenarios 360 / 420 / 450 min (A-CAP-1)."""
    eps = _episodes("test")[::3]
    tasks = []
    for ep in eps:
        runs = []
        for b in (360, 420, 450):
            for m in ("ga", "rank_first_greedy"):
                runs.append({"roster": "cf0.8", "method": m, "seed": 0, "params": {"budget": b},
                             "overrides": {"capacity": {"work_time_budget_min": b}}, "budget": b})
        tasks.append({"episode": ep, "runs": runs})
    # rosters carry their own budget column; override it per run inside the worker via a patched roster
    rows = _pool_map(_budget_task, tasks, cfg, workers)
    return _save("budget_sensitivity_test", rows, {"split": "test", "episodes": eps})


def _budget_task(task):
    from .. import runtime
    orig = runtime.load_roster

    def patched(name, borough=None, default_budget=420.0):
        crews = orig(name, borough, default_budget)
        from dataclasses import replace
        return [replace(c, work_time_budget_min=float(G["_budget"])) for c in crews]
    rows = []
    for run in task["runs"]:
        G["_budget"] = run["budget"]
        runtime.load_roster = patched
        try:
            from . import experiments as me
            me_rows = me.static_episode_task({"episode": task["episode"], "runs": [run]})
        finally:
            runtime.load_roster = orig
        rows.extend(me_rows)
    return rows


DYN_POLICIES = [
    {"name": "GA hybrid 15 min (default)"},
    {"name": "GA periodic 5 min", "overrides": {"dynamic": {"replan_policy": "periodic", "replan_interval_min": 5}}},
    {"name": "GA periodic 15 min", "overrides": {"dynamic": {"replan_policy": "periodic", "replan_interval_min": 15}}},
    {"name": "GA periodic 30 min", "overrides": {"dynamic": {"replan_policy": "periodic", "replan_interval_min": 30}}},
    {"name": "GA hybrid 15, no stability", "overrides": {"objective": {"lambda_change": 0},
                                                          "dynamic": {"min_improvement_abs": 0, "min_improvement_rel": 0}}},
    {"name": "Rank-first greedy rolling 15", "method": "rank_first_greedy", "replan_method": "rank_first_greedy"},
    {"name": "Static GA plan + insertion only", "replan_method": "warm_insertion"},
]


def exp_dynamic(cfg: dict, workers: int = 22, seeds=(0, 1)):
    """TEST daytime episodes: rolling-horizon policies, simulated execution (cf0.8)."""
    eps = _episodes("test", daytime=True)
    tasks = [{"episode": ep, "policy": pol, "seed": s, "roster": "cf0.8"} for ep in eps for pol in DYN_POLICIES for s in seeds]
    random.Random(0).shuffle(tasks)
    rows = _pool_map(dynamic_task, tasks, cfg, workers)
    return _save("dynamic_test", rows, {"split": "test", "episodes": eps, "policies": DYN_POLICIES, "seeds": list(seeds),
                                        "roster": "cf0.8"})


def exp_exact_gap(cfg: dict, workers: int = 1, n_instances: int = 60):
    """Optimality gap on tiny instances cut from VALIDATION episodes (exhaustive optimum)."""
    _init(cfg)
    from ..engine import optimize_plan
    from ..optimize.exact import solve_exact
    from ..optimize.ga import GAConfig, solve_ga
    from ..optimize.greedy import rank_first_greedy, travel_aware_greedy
    from ..optimize.instance import Instance
    from ..runtime import episode_requests, load_roster, static_planning_time, episode_info
    from ..schemas import Snapshot
    rng = np.random.default_rng(7)
    rows = []
    eps = _episodes("val")
    router = _router(cfg["routing"]["speed_factor"])
    while len(rows) < n_instances:
        ep = eps[int(rng.integers(len(eps)))]
        info = episode_info(ep)
        now = static_planning_time(ep)
        q = G["provider"].prioritize(episode_requests(ep), now)
        p = optimize_plan(Snapshot(now=now, queue=q, crews=load_roster("cf0.8", info["borough"]), crew_status={}), router, cfg,
                          G["catalog"], method="rank_first_greedy", keep_instances=True, reconstruct_paths=False)
        for ag, inst in p.instances.items():
            if inst.n < 6 or inst.K < 1 or len(rows) >= n_instances:
                continue
            idx = np.sort(rng.choice(inst.n, size=6, replace=False))
            ks = np.sort(rng.choice(inst.K, size=min(2, inst.K), replace=False))
            nodes = list(idx) + [inst.n + k for k in ks] + [inst.n + inst.K + k for k in ks]
            # capacity squeezed so that roughly half of the six requests fit (forces real choices)
            cap = np.full(len(ks), float(np.clip(inst.service[idx].sum() / (2 * len(ks)) + 20, 40, 400)))
            sub = Instance(agency=ag, req_ids=[inst.req_ids[i] for i in idx], crew_ids=[inst.crew_ids[k] for k in ks],
                           weight=inst.weight[idx], rank=inst.rank[idx], service=inst.service[idx],
                           compat=inst.compat[np.ix_(ks, idx)], M=inst.M[np.ix_(nodes, nodes)], avail=inst.avail[ks],
                           cap=cap, prev_crew=np.full(6, -1), lambda_backlog=inst.lambda_backlog,
                           lambda_delay=inst.lambda_delay, lambda_change=inst.lambda_change)
            t0 = time.perf_counter(); opt = solve_exact(sub); t_ex = time.perf_counter() - t0
            t0 = time.perf_counter(); ga = solve_ga(sub, GAConfig.from_dict(cfg["ga"]), int(rng.integers(1e6))).best; t_ga = time.perf_counter() - t0
            rf, ta = rank_first_greedy(sub), travel_aware_greedy(sub)
            f = opt.fitness()
            rows.append({"episode": ep, "agency": ag, "crews": len(ks), "optimum": f,
                         "ga": ga.fitness(), "rank_first": rf.fitness(), "travel_aware": ta.fitness(),
                         "gap_ga_pct": 100 * (ga.fitness() - f) / f, "gap_rank_first_pct": 100 * (rf.fitness() - f) / f,
                         "gap_travel_aware_pct": 100 * (ta.fitness() - f) / f, "exact_s": t_ex, "ga_s": t_ga})
        router.clear_cache()
    return _save("exact_gap_val", rows, {"split": "val", "instances": n_instances, "size": "6 requests x <=2 crews"})


def exp_routing(cfg: dict, workers: int = 1):
    """A* (haversine / ALT) vs Dijkstra point-to-point, and matrix backends, at the configured speed factor."""
    from ..roadnet.prepare import validate_routing
    from ..runtime import load_network
    net = load_network(cfg["paths"]["network"])
    v = validate_routing(net, 500, 20261019, speed_factor=float(cfg["routing"]["speed_factor"]))
    _init(cfg)
    router = _router(cfg["routing"]["speed_factor"])
    rng = np.random.default_rng(3)
    rows = []
    for n in (10, 25, 50):
        nodes = list(rng.choice(router.scc_nodes, size=n, replace=False))
        router.clear_cache()
        t0 = time.perf_counter(); d = router.matrix_minutes(nodes, nodes, "dijkstra"); td = time.perf_counter() - t0
        t0 = time.perf_counter(); a = router.matrix_minutes(nodes, nodes, "astar"); ta = time.perf_counter() - t0
        rows.append({"n_nodes": n, "pairs": n * n, "dijkstra_one_to_many_s": td, "astar_pairwise_s": ta,
                     "max_abs_diff_min": float(np.max(np.abs(a - d)))})
    df = _save("routing_matrix_benchmark", rows, {"point_to_point": v})
    (OUT / "routing_point_to_point.json").write_text(json.dumps(v, indent=2))
    return df


def exp_all(cfg: dict, workers: int = 20):
    for f in (exp_routing, exp_exact_gap, exp_static, exp_speed, exp_budget, exp_dynamic):
        print(f"=== {f.__name__}")
        f(cfg, workers=workers)
