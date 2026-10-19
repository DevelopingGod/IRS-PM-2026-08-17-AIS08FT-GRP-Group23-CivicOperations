"""Research data preparation (run once; outputs are versioned files inside the component).

1. ``build_replay_episodes``: copies ONLY input-safe columns of the authorised historical NYC311
   extract into ``data/replay/requests_windows.parquet`` and defines replay episodes
   (= sampled window x split; windows that straddle a split boundary are cut at it).
2. ``generate_crews_v2``: workforce rosters sized from TRAIN episodes only, for capacity factors
   0.6 / 0.8 / 1.0 / 1.2, reusing the original synthetic depot coordinates. The original crew file is
   never modified.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .. import timepolicy
from ..config import COMPONENT_ROOT
from ..schemas import AFTER_THE_FACT_FIELDS

INPUT_COLUMNS = ["request_id", "unique_key", "created_date", "agency", "complaint_type", "descriptor",
                 "location_type", "incident_zip", "community_board", "borough", "latitude", "longitude",
                 "geo_valid", "channel", "request_type_code", "request_type_label", "complaint_family",
                 "required_skill", "est_service_minutes", "sample_window_id", "split"]
TZ = "America/New_York"
CAPACITY_FACTORS = (0.6, 0.8, 1.0, 1.2)
TRAVEL_ALLOWANCE_MIN = 15.0      # A-CREW-2: per-job travel allowance used ONLY for workforce sizing


def build_replay_episodes(requests_parquet: str | Path, out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir or COMPONENT_ROOT / "data" / "replay")
    out_dir.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(requests_parquet)
    leaked = [c for c in INPUT_COLUMNS if c in AFTER_THE_FACT_FIELDS]
    assert not leaked, leaked
    df = raw[INPUT_COLUMNS].copy()
    # NYC Open Data timestamps are floating local times (assumption A-DATA-1): localise to New York.
    local = df["created_date"].dt.tz_localize(TZ, ambiguous="NaT", nonexistent="shift_forward")
    n_ambiguous = int(local.isna().sum())
    local = local.fillna(df["created_date"].dt.tz_localize(TZ, ambiguous=False, nonexistent="shift_forward"))
    df["created_date"] = local
    df["episode_id"] = df["sample_window_id"] + "__" + df["split"]
    df.to_parquet(out_dir / "requests_windows.parquet", index=False)
    ep = df.groupby("episode_id").agg(split=("split", "first"), window=("sample_window_id", "first"),
                                      borough=("borough", lambda s: s.mode().iat[0]), t0=("created_date", "min"),
                                      t1=("created_date", "max"), n=("request_id", "size"),
                                      n_geo_invalid=("geo_valid", lambda s: int((~s).sum()))).reset_index()
    ep["start_hour"] = ep["t0"].dt.hour
    ep["daytime"] = ep["start_hour"].between(6, 16)
    ep.to_parquet(out_dir / "episodes.parquet", index=False)
    report = {"source": str(requests_parquet), "rows": int(len(df)), "columns_kept": INPUT_COLUMNS,
              "columns_excluded_after_the_fact": [c for c in AFTER_THE_FACT_FIELDS if c in raw.columns],
              "other_columns_not_copied": sorted(set(raw.columns) - set(INPUT_COLUMNS)),
              "timezone_assumption": TZ, "dst_ambiguous_timestamps_resolved_as_standard_time": n_ambiguous,
              "episodes": int(len(ep)), "episodes_by_split": ep["split"].value_counts().to_dict(),
              "straddling_windows_cut": int(df.groupby("sample_window_id")["split"].nunique().gt(1).sum())}
    (out_dir / "replay_build_report.json").write_text(json.dumps(report, indent=2, default=str))
    return report


def _skill_groups_from_original(orig: pd.DataFrame) -> dict:
    """Multi-skill combinations that exist in the original synthetic roster, per agency."""
    combos: dict = {}
    for ag, sk in zip(orig["agency"], orig["skills"]):
        if len(sk) > 1:
            combos.setdefault(ag, set()).add(tuple(sorted(sk)))
    return combos


def _largest_remainder(shares: dict, n: int) -> dict:
    total = sum(shares.values())
    if total <= 0 or n <= 0:
        return {k: 0 for k in shares}
    raw = {k: n * v / total for k, v in shares.items()}
    out = {k: int(math.floor(v)) for k, v in raw.items()}
    rest = n - sum(out.values())
    for k in sorted(raw, key=lambda k: (-(raw[k] - out[k]), k))[:rest]:
        out[k] += 1
    return out


# Bronx and Brooklyn both start with "BRO", so the first three letters gave the same crew ID
# to crews in different boroughs. Each borough gets its own code.
BOROUGH_CODE = {"MANHATTAN": "MAN", "BRONX": "BRX", "BROOKLYN": "BKN", "QUEENS": "QUE", "STATEN ISLAND": "STA"}


def generate_crews_v2(cfg: dict, original_crews: str | Path, out_dir: Path | None = None) -> dict:
    out_dir = Path(out_dir or COMPONENT_ROOT / "data" / "crews")
    out_dir.mkdir(parents=True, exist_ok=True)
    req = pd.read_parquet(COMPONENT_ROOT / "data" / "replay" / "requests_windows.parquet")
    eps = pd.read_parquet(COMPONENT_ROOT / "data" / "replay" / "episodes.parquet")
    orig = pd.read_parquet(original_crews)
    budget = float(cfg["capacity"]["work_time_budget_min"])
    train = req[req["split"] == "train"]
    train_eps = eps[eps["split"] == "train"]
    combos = _skill_groups_from_original(orig)

    # per (agency, borough): mean over train episodes of (demand minutes / per-crew available minutes)
    need_rows = []
    agencies = sorted(train["agency"].dropna().unique())
    agencies = [a for a in agencies if a in set(orig["agency"])]
    skill_minutes = {}
    for _, e in train_eps.iterrows():
        sub = train[train["episode_id"] == e["episode_id"]]
        for ag in agencies:
            s, en = timepolicy.service_period(cfg, ag, e["t1"].to_pydatetime())
            per_crew = min(budget, (en - s).total_seconds() / 60.0)
            a = sub[sub["agency"] == ag]
            demand = float(a["est_service_minutes"].sum() + TRAVEL_ALLOWANCE_MIN * len(a))
            need_rows.append({"agency": ag, "borough": e["borough"], "episode_id": e["episode_id"],
                              "demand_min": demand, "per_crew_min": per_crew, "crew_equiv": demand / per_crew})
            for sk, g in a.groupby("required_skill"):
                key = (ag, e["borough"], sk)
                skill_minutes[key] = skill_minutes.get(key, 0.0) + float(g["est_service_minutes"].sum() + TRAVEL_ALLOWANCE_MIN * len(g))
    need = pd.DataFrame(need_rows)
    stats = need.groupby(["agency", "borough"]).agg(mean_crew_equiv=("crew_equiv", "mean"),
                                                   p90_crew_equiv=("crew_equiv", lambda s: float(np.percentile(s, 90))),
                                                   episodes=("episode_id", "nunique"),
                                                   mean_demand_min=("demand_min", "mean")).reset_index()
    report = {"method": "crews(agency,borough) = max(1, round(cf * mean_train_episodes(demand_min / per_crew_min)))",
              "travel_allowance_min_per_job": TRAVEL_ALLOWANCE_MIN, "work_time_budget_min": budget,
              "capacity_factors": list(CAPACITY_FACTORS), "train_episodes": int(len(train_eps)),
              "skill_combinations_allowed": {a: [list(c) for c in sorted(v)] for a, v in combos.items()},
              "rosters": {}}
    for cf in CAPACITY_FACTORS:
        rows = []
        for _, st in stats.iterrows():
            ag, bo = st["agency"], st["borough"]
            n = max(1, int(math.floor(cf * st["mean_crew_equiv"] + 0.5)))
            skills = {sk: m for (a, b, sk), m in skill_minutes.items() if a == ag and b == bo and m > 0}
            if not skills:
                continue
            if n >= len(skills):     # at least one crew per demanded skill, rest by demand share
                alloc = {k: 1 for k in skills}
                extra = _largest_remainder(skills, n - len(skills))
                alloc = {k: alloc[k] + extra[k] for k in skills}
            else:
                alloc = _largest_remainder(skills, n)
            crew_skills = [[sk] for sk, c in sorted(alloc.items()) for _ in range(c)]
            uncovered = [sk for sk, c in alloc.items() if c == 0]
            for sk in sorted(uncovered, key=lambda k: -skills[k]):   # cover via observed multi-skill combos
                for cs in crew_skills:
                    if any(sk in combo and set(cs) <= set(combo) for combo in combos.get(ag, ())):
                        cs.append(sk)
                        break
            depots = orig[(orig["agency"] == ag) & (orig["borough"] == bo)][["depot_lat", "depot_lon"]].drop_duplicates().values
            if len(depots) == 0:
                depots = orig[orig["agency"] == ag][["depot_lat", "depot_lon"]].drop_duplicates().values
            for j, cs in enumerate(crew_skills):
                rows.append({"crew_id": f"V2-{ag}-{BOROUGH_CODE.get(bo, bo[:3].upper())}-{j + 1:02d}", "agency": ag, "borough": bo,
                             "skills": sorted(set(cs)), "depot_lat": float(depots[j % len(depots)][0]),
                             "depot_lon": float(depots[j % len(depots)][1]), "work_time_budget_min": budget,
                             "capacity_factor": cf, "is_synthetic": True, "generator": "crews_v2"})
        df = pd.DataFrame(rows)
        path = out_dir / f"crews_v2_cf{cf:.1f}.parquet"
        df.to_parquet(path, index=False)
        covered = {(r["agency"], r["borough"], s) for _, r in df.iterrows() for s in r["skills"]}
        gaps = sorted(f"{a}/{b}/{s}" for (a, b, s) in skill_minutes if (a, b, s) not in covered)
        report["rosters"][f"{cf:.1f}"] = {"file": path.name, "crews": int(len(df)),
                                          "by_agency": df["agency"].value_counts().to_dict(),
                                          "by_borough": df["borough"].value_counts().to_dict(),
                                          "multi_skill_crews": int(df["skills"].apply(len).gt(1).sum()),
                                          "uncovered_agency_borough_skill": gaps}
    legacy = orig.copy()
    legacy["work_time_budget_min"] = budget
    legacy = legacy[["crew_id", "agency", "borough", "skills", "depot_lat", "depot_lon", "work_time_budget_min"]]
    legacy["generator"] = "original_v1_with_budget (shifts, avg_speed_kmh, max_jobs_per_shift dropped)"
    legacy.to_parquet(out_dir / "crews_legacy_v1_budget.parquet", index=False)
    report["legacy_roster"] = {"file": "crews_legacy_v1_budget.parquet", "crews": int(len(legacy))}
    stats.to_csv(out_dir / "workforce_sizing_train.csv", index=False)
    (out_dir / "crews_v2_generation_report.json").write_text(json.dumps(report, indent=2))
    return report
