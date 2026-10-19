"""Scripted end-to-end demonstrations on the REAL road network and historical request windows.

``python -m crew_dispatch demo`` writes everything under ``outputs/demos/`` and a summary
``outputs/demos/demo_summary.json`` that points to the concrete plan versions / maps showing:
  D1 a static proposed plan (all agencies, road-following maps, per-crew panels)
  D2 a crew-shortage / backlog case (capacity factor 0.6 vs 1.2 on the same window)
  D3 a dynamic replay with: a new request that changes C11 ranks and triggers a published replan,
     replanning while EN_ROUTE / IN_PROGRESS jobs stay locked, a crew breakdown, a cancellation,
     a held request and a human-review completion (review/hold flags and all execution events
     are SIMULATED and labelled as such)
  D4 off-hours queueing under the operating-hours policy (night window, plan at opening)
"""
from __future__ import annotations

import json
import re
import shutil
from datetime import timedelta
from pathlib import Path

from ..config import COMPONENT_ROOT, deep_merge
from ..dynamic.controller import DispatchController
from ..dynamic.simulator import ReplaySimulator
from ..runtime import data_versions, episode_info, episode_requests, load_roster, load_runtime, static_planning_time
from ..viz.maps import BaseMap, render_crew_panels

OUT = COMPONENT_ROOT / "outputs" / "demos"
STATIC_EP = "MAN-2026090612__test"
DYNAMIC_EP = "STA-2026082208__test"
NIGHT_EP = "QUE-2026091100__test"


def _fresh(name: str) -> Path:
    d = OUT / name
    if d.exists():
        shutil.rmtree(d)
    return d


def _static(cfg, router, catalog, provider, log, base, name, ep, roster, maps=True):
    out = _fresh(name)
    info = episode_info(ep)
    ctl = DispatchController(load_roster(roster, info["borough"]), router, cfg, catalog, out, run_id=name, seed=0,
                             c11_info={**provider.info, "resolution_log": log}, data_versions=data_versions(cfg, roster),
                             maps=maps, base_map=base)
    now = static_planning_time(ep)
    ctl.start_duty(now)
    q = provider.prioritize(episode_requests(ep), now)
    res = ctl.handle({"type": "QUEUE_UPDATED", "time": now, "queue": q, "trigger": "static_plan",
                      "source": provider.info["provider"], "simulated": False})
    return ctl, res, out


def run_all_demos(cfg: dict) -> dict:
    router, catalog, provider, log = load_runtime(cfg)
    base = BaseMap(router.net)
    summary = {"c11_provider": provider.info, "c11_resolution_log": log, "demos": {}}

    # D1 static plan --------------------------------------------------------------------------
    ctl, res, out = _static(cfg, router, catalog, provider, log, base, "D1_static_plan", STATIC_EP, "cf1.0")
    panels = render_crew_panels(ctl.doc, ctl.geo, router.net, out / "maps" / "crew_panels_NYPD.png", agency="NYPD", base=base)
    summary["demos"]["D1_static_plan"] = {"episode": STATIC_EP, "roster": "cf1.0", "plan": str(out / "plans" / "plan_v0001.json"),
                                          "maps": res.map_paths + [str(panels)], "metrics": ctl.doc["metrics"]}

    # D2 shortage / backlog -------------------------------------------------------------------
    d2 = {}
    for roster in ("cf0.6", "cf1.2"):
        c, r, o = _static(cfg, router, catalog, provider, log, base, f"D2_shortage_{roster}", STATIC_EP, roster)
        m = c.doc["metrics"]
        d2[roster] = {"crews": len(c.crews), "served": m["served"], "candidates": m["provisional_candidates"],
                      "weighted_served_share": m["weighted_served_share"], "reasons": m["unserved_reason_counts"],
                      "plan": str(o / "plans" / "plan_v0001.json"), "maps": r.map_paths}
    summary["demos"]["D2_shortage_backlog"] = d2

    # D3 dynamic replay with scripted (simulated) events --------------------------------------
    out = _fresh("D3_dynamic_replay")
    info = episode_info(DYNAMIC_EP)
    arrivals = episode_requests(DYNAMIC_EP)
    # Simulated upstream flags (labelled): two requests arrive as provisional_review, one as hold.
    flagged = {"review": [], "hold": []}
    for r in arrivals:
        if r["agency"] == "NYPD" and len(flagged["review"]) < 2 and r["created_date"] > info["t0"] + timedelta(minutes=20):
            r["c11_queue_status"] = "provisional_review"
            r["demo_simulated_flag"] = "provisional_review (simulated upstream review gate for the demo)"
            flagged["review"].append(r["request_id"])
        elif r["agency"] == "HPD" and not flagged["hold"]:
            r["c11_queue_status"] = "hold"
            r["demo_simulated_flag"] = "hold (simulated upstream hold for the demo)"
            flagged["hold"].append(r["request_id"])
    crews = load_roster("cf0.8", info["borough"])
    dcfg = deep_merge(cfg, {"maps": {"max_maps_per_run": 60}})
    ctl = DispatchController(crews, router, dcfg, catalog, out, run_id="D3_dynamic_replay", seed=0,
                             c11_info={**provider.info, "resolution_log": log}, data_versions=data_versions(cfg, "cf0.8"),
                             maps=True, base_map=base)
    ctl.start_duty(info["t0"])
    scen = [{"type": "CREW_STATUS", "time": info["t0"] + timedelta(minutes=95), "crew_id": "AUTO_BUSIEST", "available": False,
             "note": "vehicle breakdown (simulated)"},
            {"type": "REVIEW_COMPLETED", "time": info["t0"] + timedelta(minutes=75), "request_id": flagged["review"][0],
             "new_status": "provisional"},
            {"type": "CANCEL", "time": info["t0"] + timedelta(minutes=50), "request_id": "AUTO_PLANNED"}]
    sim = ReplaySimulator(ctl, provider, arrivals, dcfg, seed=0, scenario_events=scen)
    end = max(st.duty_end for st in ctl.crew_status.values())
    s = sim.run(info["t0"], end)
    (out / "replay_summary.json").write_text(json.dumps(s, indent=2, default=str))
    resolved = {e["type"]: e for e in sim.resolved_scenarios}
    cancel_target = resolved.get("CANCEL", {}).get("request_id")
    # locate evidence in the published plans
    evidence = {"new_request_rank_change_replan": None, "replan_with_locked_jobs": None, "breakdown_replan": None,
                "review_completed_replan": None, "cancellation_replan": None}
    for p in sorted((out / "plans").glob("plan_v*.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        ev = d["event"]
        locked = [j for c in d["crews"] for j in c["locked_commitments"] if j["state"] in ("EN_ROUTE", "IN_PROGRESS")]
        m_rank = re.search(r"(\d+) rank changes", ev.get("why", ""))
        if evidence["new_request_rank_change_replan"] is None and ev.get("trigger") == "new_request" and d["plan_version"] > 1 \
                and d["changes_from_previous"]["newly_assigned"] and m_rank and int(m_rank.group(1)) > 0:
            evidence["new_request_rank_change_replan"] = {"plan": p.name, "why": ev["why"], "changes": {k: v for k, v in d["changes_from_previous"].items()}}
        if evidence["replan_with_locked_jobs"] is None and locked and d["changes_from_previous"]["material_change"]:
            evidence["replan_with_locked_jobs"] = {"plan": p.name, "locked_en_route_or_in_progress": locked[:6], "why": ev["why"]}
        if evidence["breakdown_replan"] is None and ev["type"] == "CREW_STATUS":
            evidence["breakdown_replan"] = {"plan": p.name, "why": ev["why"], "forced_reasons": ev.get("forced_reasons")}
        if evidence["review_completed_replan"] is None and flagged["review"][0] in {a["request_id"] for a in d["assignments"]}:
            evidence["review_completed_replan"] = {"plan": p.name, "request_id": flagged["review"][0], "why": ev["why"]}
        if evidence["cancellation_replan"] is None and ev.get("trigger") == "cancellation":
            evidence["cancellation_replan"] = {"plan": p.name, "request_id": cancel_target, "why": ev["why"]}
    summary["demos"]["D3_dynamic_replay"] = {"episode": DYNAMIC_EP, "roster": "cf0.8", "simulated_flags": flagged,
                                             "scenario_events_resolved": sim.resolved_scenarios, "summary": s, "evidence": evidence,
                                             "plans": len(list((out / "plans").glob("plan_v*.json"))),
                                             "maps": len(list((out / "maps").glob("*.png"))) if (out / "maps").exists() else 0,
                                             "events_log": str(out / "events" / "events.jsonl")}

    # D4 off-hours queueing (night window) -----------------------------------------------------
    out = _fresh("D4_night_window")
    info = episode_info(NIGHT_EP)
    crews = load_roster("cf0.8", info["borough"])
    ncfg = deep_merge(cfg, {"maps": {"max_maps_per_run": 16}})
    ctl = DispatchController(crews, router, ncfg, catalog, out, run_id="D4_night_window", seed=0,
                             c11_info={**provider.info, "resolution_log": log}, data_versions=data_versions(cfg, "cf0.8"),
                             maps=True, base_map=base)
    ctl.start_duty(info["t0"])
    sim = ReplaySimulator(ctl, provider, episode_requests(NIGHT_EP), ncfg, seed=0)
    end = max(st.duty_end for st in ctl.crew_status.values())
    s = sim.run(info["t0"], end)
    duty = {}
    for cid, st in ctl.crew_status.items():
        duty.setdefault(ctl.crews[cid].agency, (st.duty_start.isoformat(), st.duty_end.isoformat()))
    summary["demos"]["D4_night_window"] = {"episode": NIGHT_EP, "window_start": info["t0"], "duty_periods_by_agency": duty,
                                           "summary": s}
    (OUT / "demo_summary.json").write_text(json.dumps(summary, indent=2, default=str))
    print(json.dumps({k: (v.get("evidence") if isinstance(v, dict) and "evidence" in v else "ok") for k, v in summary["demos"].items()},
                     indent=2, default=str))
    return summary
