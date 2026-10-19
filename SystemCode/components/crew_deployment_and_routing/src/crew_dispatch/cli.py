"""Command line: ``python -m crew_dispatch <command>`` (see README)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from .config import COMPONENT_ROOT, load_config


def _run_id(prefix: str) -> str:
    return f"{prefix}_{datetime.now().strftime('%Y%m%dT%H%M%S')}"


def cmd_prepare_network(a):
    from .roadnet.prepare import prepare_network
    rep = prepare_network(COMPONENT_ROOT, n_validation_pairs=a.pairs)
    print(json.dumps(rep["validation"], indent=2))


def cmd_prepare_data(a):
    from .research.data_prep import build_replay_episodes, generate_crews_v2
    print(json.dumps(build_replay_episodes(a.requests), indent=2, default=str))
    rep = generate_crews_v2(load_config(a.config), a.crews)
    print(json.dumps({k: v["crews"] for k, v in rep["rosters"].items()}, indent=2))


def cmd_plan(a):
    """Static proposal for one replay episode (C11 run once at the end of the window)."""
    from .dynamic.controller import DispatchController
    from .runtime import data_versions, episode_info, episode_requests, load_roster, load_runtime, static_planning_time
    cfg = load_config(a.config, {"ga": {"time_limit_s": a.time_limit}} if a.time_limit else None)
    router, catalog, provider, log = load_runtime(cfg)
    info = episode_info(a.episode)
    crews = load_roster(a.roster, info["borough"])
    run_id = a.run_id or _run_id(f"plan_{a.episode}_{a.roster}_{a.method}")
    out = COMPONENT_ROOT / "outputs" / "runs" / run_id
    ctl = DispatchController(crews, router, cfg, catalog, out, run_id=run_id, seed=a.seed, method=a.method,
                             c11_info={**provider.info, "resolution_log": log}, data_versions=data_versions(cfg, a.roster))
    now = static_planning_time(a.episode)
    ctl.start_duty(now)
    queue = provider.prioritize(episode_requests(a.episode), now)
    res = ctl.handle({"type": "QUEUE_UPDATED", "time": now, "queue": queue, "trigger": "static_plan",
                      "source": provider.info["provider"], "simulated": False})
    print(json.dumps({"run_dir": str(out), "published": res.published, "version": res.plan_version,
                      "metrics": ctl.doc["metrics"] if ctl.doc else None, "maps": res.map_paths}, indent=2, default=str))


def cmd_replay(a):
    from .dynamic.controller import DispatchController
    from .dynamic.simulator import ReplaySimulator
    from .runtime import data_versions, episode_info, episode_requests, load_roster, load_runtime
    over = {"dynamic": {"replan_interval_min": a.interval, "replan_policy": a.policy}}
    cfg = load_config(a.config, over)
    router, catalog, provider, log = load_runtime(cfg)
    info = episode_info(a.episode)
    crews = load_roster(a.roster, info["borough"])
    run_id = a.run_id or _run_id(f"replay_{a.episode}_{a.roster}_{a.policy}{a.interval:g}")
    out = COMPONENT_ROOT / "outputs" / "runs" / run_id
    ctl = DispatchController(crews, router, cfg, catalog, out, run_id=run_id, seed=a.seed, method="ga",
                             replan_method=a.replan_method, c11_info={**provider.info, "resolution_log": log},
                             data_versions=data_versions(cfg, a.roster))
    sim = ReplaySimulator(ctl, provider, episode_requests(a.episode), cfg, seed=a.seed)
    start = info["t0"]
    ctl.start_duty(start)
    end_ = max(st.duty_end for st in ctl.crew_status.values())
    summary = sim.run(start, end_)
    (out / "replay_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps(summary, indent=2, default=str))


def cmd_demo(a):
    from .research.demos import run_all_demos
    run_all_demos(load_config(a.config))


def cmd_experiment(a):
    from .research import experiments
    getattr(experiments, "exp_" + a.name.replace("-", "_"))(load_config(a.config), workers=a.workers)


def cmd_verify_upstream(a):
    import runpy
    sys.argv = ["verify_upstream.py", "verify"]
    runpy.run_path(str(COMPONENT_ROOT / "scripts" / "verify_upstream.py"), run_name="__main__")


def main(argv=None):
    p = argparse.ArgumentParser(prog="crew_dispatch", description="CivicOps AI crew dispatch and route optimisation")
    p.add_argument("--config", default=None, help="YAML config (default config/default.yaml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("prepare-network"); s.add_argument("--pairs", type=int, default=300); s.set_defaults(fn=cmd_prepare_network)
    s = sub.add_parser("prepare-data"); s.add_argument("--requests", required=True); s.add_argument("--crews", default=str(COMPONENT_ROOT / "data/crews/synthetic_crews_original_v1.parquet")); s.set_defaults(fn=cmd_prepare_data)
    s = sub.add_parser("plan"); s.add_argument("--episode", required=True); s.add_argument("--roster", default="cf1.0")
    s.add_argument("--method", default="ga"); s.add_argument("--seed", type=int, default=0); s.add_argument("--run-id")
    s.add_argument("--time-limit", type=float); s.set_defaults(fn=cmd_plan)
    s = sub.add_parser("replay"); s.add_argument("--episode", required=True); s.add_argument("--roster", default="cf0.8")
    s.add_argument("--policy", default="hybrid", choices=["hybrid", "periodic", "event"]); s.add_argument("--interval", type=float, default=15)
    s.add_argument("--replan-method", default="ga"); s.add_argument("--seed", type=int, default=0); s.add_argument("--run-id"); s.set_defaults(fn=cmd_replay)
    s = sub.add_parser("demo"); s.set_defaults(fn=cmd_demo)
    s = sub.add_parser("experiment"); s.add_argument("name"); s.add_argument("--workers", type=int, default=20); s.set_defaults(fn=cmd_experiment)
    s = sub.add_parser("verify-upstream"); s.set_defaults(fn=cmd_verify_upstream)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main()
