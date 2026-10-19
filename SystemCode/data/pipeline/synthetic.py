"""Step 3: the ONE shared synthetic population, generated once with seed 2026.

Every table written here has is_synthetic=True and the prefix synthetic_ in its
file name. The report, dashboard and every chart must say so. Nothing
synthetic is ever passed off as real 311 data.

Tables (data/processed/):
  synthetic_citizens.parquet          5,000 citizens. hidden_honesty is for EVALUATION ONLY
  synthetic_reporter_links.parquet    real request_id -> synthetic citizen_id
  synthetic_burst_requests.parquet    extra spam bursts from burst reporters (C06 reporter_burst_flag)
  synthetic_crews.parquet             about 100 crews: agency, borough, skills, depot, capacity (no shift, by team decision)
  synthetic_urgency_scenarios.parquet 1,000 labelled urgency cases (C10) from an independent rubric
  synthetic_queue_snapshots.parquet   100 queue snapshots with an expert reference rank (C11)
  synthetic_resolution_labels.parquet 1,000 labelled resolution-time cases (C09); real closed_date
                                       coverage is too thin and uneven to train on directly, so this
                                       gives C09 a properly sized, borough- and weather-aware label set
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .config import PROCESSED, SEED, load_catalog

ARCHETYPES = {
    # name: (share, beta_a, beta_b, activity_scale, leaderboard_opt_in)
    "OCCASIONAL_HONEST": (0.70, 18.0, 2.0, 1.0, 0.30),
    "FREQUENT_HONEST": (0.12, 20.0, 2.0, 6.0, 0.55),
    "FREQUENT_UNRELIABLE": (0.08, 3.0, 5.0, 5.0, 0.10),
    "BURST_REPORTER": (0.03, 1.5, 6.0, 3.0, 0.05),
    "NEW_HONEST": (0.07, 9.0, 1.0, 0.3, 0.30),
}

AGENCY_SKILLS = {
    "DOT": [["ROAD_REPAIR"], ["ROAD_REPAIR", "SIDEWALK_REPAIR"], ["STREET_LIGHTING", "TRAFFIC_SIGNALS"],
            ["TRAFFIC_SIGNALS"], ["STREET_LIGHTING"], ["SIDEWALK_REPAIR"]],
    "DEP": [["SEWER_DRAINAGE"], ["WATER_MAINS"], ["SEWER_DRAINAGE", "WATER_MAINS"]],
    "DSNY": [["STREET_CLEANING"], ["SANITATION_COLLECTION"], ["VEHICLE_REMOVAL"], ["SNOW_ICE", "STREET_CLEANING"]],
    "DPR": [["FORESTRY"]],
    "HPD": [["HOUSING_INSPECTION"]],
    "DOHMH": [["PEST_CONTROL"]],
    "NYPD": [["NOISE_PATROL", "PARKING_ENFORCEMENT"], ["PARKING_ENFORCEMENT", "VEHICLE_REMOVAL"], ["NOISE_PATROL"]],
    "DOB": [["BUILDING_INSPECTION"]],
}


# ------------------------------------------------------------------ citizens
def make_citizens(real: pd.DataFrame, n: int = 5000, rng=None) -> pd.DataFrame:
    rng = rng or np.random.default_rng(SEED)
    names = list(ARCHETYPES)
    shares = np.array([ARCHETYPES[k][0] for k in names])
    arche = rng.choice(names, size=n, p=shares / shares.sum())
    b_share = real["borough"].value_counts(normalize=True)
    borough = rng.choice(b_share.index.to_numpy(), size=n, p=b_share.to_numpy())
    honesty = np.array([rng.beta(ARCHETYPES[a][1], ARCHETYPES[a][2]) for a in arche])
    activity = np.array([ARCHETYPES[a][3] for a in arche]) * (rng.pareto(2.5, n) + 1)
    geo = real[real["geo_valid"]]
    home_lat, home_lon = np.empty(n), np.empty(n)
    for b in np.unique(borough):
        idx = np.where(borough == b)[0]
        pts = geo[geo["borough"] == b][["latitude", "longitude"]].to_numpy()
        pick = pts[rng.integers(0, len(pts), len(idx))]
        home_lat[idx] = pick[:, 0] + rng.normal(0, 0.002, len(idx))
        home_lon[idx] = pick[:, 1] + rng.normal(0, 0.002, len(idx))
    t_end = real["created_at"].max()
    days = rng.integers(30, 1500, n)
    new = arche == "NEW_HONEST"
    days[new] = rng.integers(1, 60, new.sum())
    joined = t_end - pd.to_timedelta(days, unit="D")
    return pd.DataFrame({
        "citizen_id": [f"CIT-{i:06d}" for i in range(1, n + 1)],
        "pseudonym": [f"Resident {rng.integers(1000, 9999)}-{i}" for i in range(1, n + 1)],  # no real PII, ever
        "home_borough": borough,
        "home_lat": home_lat.round(6), "home_lon": home_lon.round(6),
        "archetype": arche,
        "hidden_honesty": honesty.round(4),   # EVALUATION ONLY: components must never read this column
        "activity_weight": activity.round(4),
        "leaderboard_opt_in": rng.random(n) < np.array([ARCHETYPES[a][4] for a in arche]),
        "joined_at": joined,
        "is_synthetic": True,
    })


# ------------------------------------------------------------------ reporters on real rows
def link_reporters(real: pd.DataFrame, citizens: pd.DataFrame, rng=None) -> pd.DataFrame:
    """Assign each real request to a synthetic citizen in the same borough. The
    probability is activity x P(observed outcome | hidden honesty), so a
    citizen's hidden honesty is consistent with the real outcomes they are
    linked to. That consistency is what makes the credibility MAE test meaningful."""
    rng = rng or np.random.default_rng(SEED + 1)
    out = []
    for b, grp in real.groupby("borough"):
        cit = citizens[citizens["home_borough"] == b]
        base = cit["activity_weight"].to_numpy()
        h = cit["hidden_honesty"].to_numpy()
        for outcome, rows in grp.groupby("verified_outcome"):
            like = h if outcome == "GENUINE" else (1 - h) if outcome == "UNFOUNDED" else np.ones_like(h)
            p = base * like
            p = p / p.sum()
            pick = rng.choice(cit["citizen_id"].to_numpy(), size=len(rows), p=p)
            out.append(pd.DataFrame({"request_id": rows["request_id"].to_numpy(), "citizen_id": pick}))
    links = pd.concat(out, ignore_index=True)
    links["link_is_synthetic"] = True
    return links


def make_bursts(real: pd.DataFrame, citizens: pd.DataFrame, rng=None) -> pd.DataFrame:
    """Burst reporters file 3 to 6 near-identical reports within an hour. These rows are fully synthetic."""
    rng = rng or np.random.default_rng(SEED + 2)
    bursters = citizens[citizens["archetype"] == "BURST_REPORTER"]
    geo = real[real["geo_valid"] & (real["split"] == "train")]
    rows = []
    for c in bursters.itertuples():
        seed_rows = geo[geo["borough"] == c.home_borough].sample(int(rng.integers(1, 4)), random_state=int(rng.integers(1e9)))
        for s in seed_rows.itertuples():
            k = int(rng.integers(3, 7))
            for j in range(k):
                rows.append({
                    "request_id": f"SR-B{len(rows):07d}", "citizen_id": c.citizen_id, "borough": s.borough,
                    "request_type_code": s.request_type_code, "complaint_type": s.complaint_type,
                    "descriptor": s.descriptor, "complaint_family": s.complaint_family,
                    "created_at": s.created_at + pd.Timedelta(minutes=int(rng.integers(0, 60))),
                    "latitude": s.latitude + rng.normal(0, 0.0008), "longitude": s.longitude + rng.normal(0, 0.0008),
                    "channel": rng.choice(["ONLINE", "MOBILE"]),
                    "verified_outcome": "UNFOUNDED" if rng.random() > c.hidden_honesty else "GENUINE",
                    "burst_id": f"BURST-{c.citizen_id}-{s.Index}", "split": s.split,
                    "source": "SYNTHETIC", "is_synthetic": True,
                })
    return pd.DataFrame(rows)


# ------------------------------------------------------------------ crews
def make_crews(real: pd.DataFrame, total: int = 100, rng=None) -> pd.DataFrame:
    rng = rng or np.random.default_rng(SEED + 3)
    vol = real.groupby(["borough", "catalog_agency"]).size()
    share = vol / vol.sum()
    rows = []
    geo = real[real["geo_valid"]]
    for (b, a), s in share.items():
        n = int(np.clip(round(s * total), 1, 8))
        templates = AGENCY_SKILLS[a]
        pts = geo[geo["borough"] == b][["latitude", "longitude"]].to_numpy()
        for i in range(max(n, len(templates) if s > 0.01 else 1)):
            skills = templates[i % len(templates)]
            depot = pts[rng.integers(0, len(pts))]
            rows.append({
                "crew_id": f"CRW-{a}-{len(rows) + 1:03d}", "agency": a, "borough": b, "skills": list(skills),
                "depot_lat": round(float(depot[0]), 6), "depot_lon": round(float(depot[1]), 6),
                "max_jobs": int(rng.integers(6, 13)), "avg_speed_kmh": float(rng.uniform(14, 24)),
                "is_synthetic": True,
            })
    crews = pd.DataFrame(rows)
    # guarantee coverage: every (borough, required skill) that appears in the data has a crew
    need = real.groupby(["borough", "required_skill", "catalog_agency"]).size().reset_index()[["borough", "required_skill", "catalog_agency"]]
    for r in need.itertuples():
        have = crews[(crews["borough"] == r.borough) & crews["skills"].map(lambda s: r.required_skill in s)]
        if have.empty:
            pts = geo[geo["borough"] == r.borough][["latitude", "longitude"]].to_numpy()
            depot = pts[rng.integers(0, len(pts))]
            crews.loc[len(crews)] = {"crew_id": f"CRW-{r.catalog_agency}-{len(crews) + 1:03d}", "agency": r.catalog_agency,
                                     "borough": r.borough, "skills": [r.required_skill],
                                     "depot_lat": round(float(depot[0]), 6), "depot_lon": round(float(depot[1]), 6),
                                     "max_jobs": 8, "avg_speed_kmh": 18.0, "is_synthetic": True}
    return crews


# ------------------------------------------------------------------ urgency scenarios (C10)
HAZARD_TYPES = {"RT002": 0.6, "RT005": 0.5, "RT020": 0.6, "RT022": 0.5, "RT023": 0.4, "RT060": 0.4,
                "RT061": 0.3, "RT080": 0.4, "RT084": 0.3, "RT140": 0.3, "RT044": 0.3, "RT004": 0.15}
URGENCY_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]


def _rubric(row, rng) -> tuple[str, str]:
    """Independent expert rubric. It is deliberately NOT Khalid's rule set, so C10
    is not graded against its own logic. The base comes from the catalog default,
    context modifiers move it up, and 8% reviewer disagreement is added as noise."""
    level = URGENCY_ORDER.index(row["default_urgency"])
    why = [f"base {row['default_urgency']} for {row['request_type_label']}"]
    if row["hazard_indicator"]:
        level += 1; why.append("active hazard")
    if row["people_at_risk"] >= 10:
        level += 1; why.append(f"{row['people_at_risk']} people exposed")
    if row["sensitive_site_flag"] and level >= 1:
        level += 1; why.append("sensitive site nearby")
    if row["external_event_severity"] >= 0.7 and row["complaint_family"] in ("WATER_SEWER", "PARKS_TREES", "STREETS"):
        level += 1; why.append("severe weather amplifies risk")
    if row["request_type_code"] in ("RT080",) and row["wx_tmin_c"] is not None and row["wx_tmin_c"] < 0:
        level += 1; why.append("no heat below freezing")
    level = int(np.clip(level, 0, 3))
    if rng.random() < 0.08:  # a real reviewer panel disagrees sometimes
        level = int(np.clip(level + rng.choice([-1, 1]), 0, 3)); why.append("reviewer judgement")
    return URGENCY_ORDER[level], "; ".join(why)


def make_urgency_scenarios(real: pd.DataFrame, n: int = 1000, rng=None) -> pd.DataFrame:
    rng = rng or np.random.default_rng(SEED + 4)
    base = real[real["geo_valid"]].copy()
    w = base["request_type_code"].map(lambda c: 3.0 if c in HAZARD_TYPES else 1.0)
    pick = base.sample(n, weights=w, random_state=SEED).reset_index(drop=True)
    hz = pick["request_type_code"].map(lambda c: HAZARD_TYPES.get(c, 0.05))
    pick["hazard_indicator"] = rng.random(n) < hz
    pick["sensitive_site_flag"] = rng.random(n) < 0.18
    pick["people_at_risk"] = np.where(pick["hazard_indicator"], rng.integers(1, 60, n), rng.integers(0, 6, n))
    precip = pick.get("wx_precip_mm", pd.Series(0, index=pick.index)).fillna(0)
    pick["external_event_severity"] = np.clip(precip / 40 + rng.normal(0, 0.08, n), 0, 1).round(3)
    if "wx_tmin_c" not in pick:
        pick["wx_tmin_c"] = None
    labels = [_rubric(r, rng) for _, r in pick.iterrows()]
    pick["urgency_ground_truth"] = [l for l, _ in labels]
    pick["reviewer_rationale"] = [w for _, w in labels]
    pick["scenario_id"] = [f"US-{i:04d}" for i in range(1, n + 1)]
    pick["is_synthetic"] = True  # the labels and context are synthetic; the request fields are real
    pick["eval_split"] = np.where(rng.random(n) < 0.3, "test", "train")
    keep = ["scenario_id", "request_id", "borough", "request_type_code", "request_type_label", "complaint_family",
            "complaint_type", "descriptor", "location_type", "latitude", "longitude", "created_at",
            "hazard_indicator", "sensitive_site_flag", "people_at_risk", "external_event_severity",
            "urgency_ground_truth", "reviewer_rationale", "eval_split", "is_synthetic"]
    return pick[keep]


# ------------------------------------------------------------------ resolution-time labels (C09)
def make_resolution_scenarios(real: pd.DataFrame, n: int = 1000, rng=None) -> pd.DataFrame:
    """Real closed_date coverage (resolution_valid) is too thin and unevenly
    distributed across type and borough to train C09 on directly (Mohammad,
    18 Sep review). This mirrors the C10 approach: real request rows, an
    independent synthetic label. The label is centered on the REAL median
    resolution_hours for that request type where enough real examples exist
    (falling back to the complaint_family, then the overall, median), then
    perturbed by weather severity and noise, so it stays grounded in the real
    distribution rather than invented from nothing."""
    rng = rng or np.random.default_rng(SEED + 6)
    base = real[real["geo_valid"]].copy()

    valid = real[real["resolution_valid"]]
    by_type = valid.groupby("request_type_code")["resolution_hours"].median()
    by_family = valid.groupby("complaint_family")["resolution_hours"].median()
    overall = valid["resolution_hours"].median()

    def baseline_hours(row) -> float:
        if row["request_type_code"] in by_type.index and (valid["request_type_code"] == row["request_type_code"]).sum() >= 20:
            return float(by_type[row["request_type_code"]])
        if row["complaint_family"] in by_family.index:
            return float(by_family[row["complaint_family"]])
        return float(overall)

    w = base["request_type_code"].map(lambda c: 3.0 if c in HAZARD_TYPES else 1.0)
    pick = base.sample(n, weights=w, random_state=SEED + 6).reset_index(drop=True)
    pick["baseline_hours"] = pick.apply(baseline_hours, axis=1)
    precip = pick.get("wx_precip_mm", pd.Series(0, index=pick.index)).fillna(0)
    weather_lift = 1 + np.clip(precip / 60, 0, 0.6) * pick["complaint_family"].isin(
        ["WATER_SEWER", "STREETS", "PARKS_TREES"]).astype(float)
    noise = rng.lognormal(mean=0, sigma=0.35, size=n)
    pick["resolution_hours_ground_truth"] = (pick["baseline_hours"] * weather_lift * noise).round(1)
    pick["scenario_id"] = [f"RS-{i:04d}" for i in range(1, n + 1)]
    pick["is_synthetic"] = True  # the label is synthetic; the request fields are real
    pick["eval_split"] = np.where(rng.random(n) < 0.3, "test", "train")
    keep = ["scenario_id", "request_id", "borough", "request_type_code", "request_type_label", "complaint_family",
            "complaint_type", "descriptor", "location_type", "latitude", "longitude", "created_at",
            "baseline_hours", "resolution_hours_ground_truth", "eval_split", "is_synthetic"]
    return pick[keep]


# ------------------------------------------------------------------ queue snapshots (C11)
def make_queue_snapshots(real: pd.DataFrame, n_snapshots: int = 100, rng=None) -> pd.DataFrame:
    rng = rng or np.random.default_rng(SEED + 5)
    base = real[real["geo_valid"]].reset_index(drop=True)
    frames = []
    urg_score = {"LOW": 20, "MEDIUM": 45, "HIGH": 70, "CRITICAL": 92}
    for s in range(1, n_snapshots + 1):
        surge = s % 4 == 0
        size = int(rng.integers(120, 201) if surge else rng.integers(50, 121))
        borough = rng.choice(base["borough"].unique())
        q = base[base["borough"] == borough].sample(size, random_state=int(rng.integers(1e9))).copy()
        lvl = q["default_urgency"].to_numpy().copy()
        bump = rng.random(size) < (0.25 if surge else 0.1)
        lvl[bump] = [URGENCY_ORDER[min(URGENCY_ORDER.index(x) + 1, 3)] for x in lvl[bump]]
        q["urgency_level"] = lvl
        q["urgency_score"] = np.clip([urg_score[x] for x in lvl] + rng.normal(0, 6, size), 0, 100).round(1)
        q["duplicate_count"] = rng.choice([1, 1, 1, 2, 3, 5], size)
        q["credibility_score"] = np.clip(rng.normal(68, 18, size), 5, 99).round(1)
        q["escalation_level"] = rng.choice(["NONE", "NONE", "NONE", "SUPERVISOR", "CENTRAL_AUTHORITY"], size, p=[.5, .2, .15, .1, .05])
        q["predicted_resolution_hours"] = np.exp(rng.normal(3, 1.1, size)).round(1)
        q["external_factor_score"] = np.clip(rng.beta(1.5, 5, size) + (0.3 if surge else 0), 0, 1).round(3)
        q["waiting_minutes"] = rng.integers(5, 60 * 72, size)
        q["sla_minutes_remaining"] = (rng.integers(-600, 60 * 96, size)).astype(int)
        q["crew_capacity"] = int(rng.integers(4, 16))
        # expert reference order: urgency first, then SLA risk, then corroboration, then waiting time (with judgement noise)
        key = (q["urgency_score"] / 100 * 0.55 + (1 - np.clip(q["sla_minutes_remaining"], 0, 5760) / 5760) * 0.2
               + np.log1p(q["duplicate_count"]) / np.log(6) * 0.1 + np.clip(q["waiting_minutes"] / 4320, 0, 1) * 0.1
               + q["external_factor_score"] * 0.05 + rng.normal(0, 0.03, size))
        q["expert_priority_rank"] = (-key).rank(method="first").astype(int)
        q["snapshot_id"] = f"QS-{s:03d}"
        q["scenario"] = "SURGE" if surge else "NORMAL"
        q["eval_split"] = "test" if s > 70 else "train"
        frames.append(q[["snapshot_id", "scenario", "eval_split", "request_id", "borough", "request_type_code",
                         "complaint_family", "latitude", "longitude", "urgency_level", "urgency_score",
                         "duplicate_count", "credibility_score", "escalation_level", "predicted_resolution_hours",
                         "external_factor_score", "waiting_minutes", "sla_minutes_remaining", "crew_capacity",
                         "est_service_minutes", "required_skill", "expert_priority_rank"]])
    out = pd.concat(frames, ignore_index=True)
    out["is_synthetic"] = True
    return out


def main() -> None:
    real = pd.read_parquet(PROCESSED / "requests_real.parquet")
    rng = np.random.default_rng(SEED)
    citizens = make_citizens(real, rng=rng)
    links = link_reporters(real, citizens)
    bursts = make_bursts(real, citizens)
    crews = make_crews(real)
    urgency = make_urgency_scenarios(real)
    queues = make_queue_snapshots(real)
    resolution = make_resolution_scenarios(real)
    for name, df in [("synthetic_citizens", citizens), ("synthetic_reporter_links", links),
                     ("synthetic_burst_requests", bursts), ("synthetic_crews", crews),
                     ("synthetic_urgency_scenarios", urgency), ("synthetic_queue_snapshots", queues),
                     ("synthetic_resolution_labels", resolution)]:
        df.to_parquet(PROCESSED / f"{name}.parquet", index=False)
        print(f"{name:34} {len(df):>7,} rows")
    print(crews.groupby(["borough", "agency"]).size().unstack(fill_value=0).to_string())
    print(urgency["urgency_ground_truth"].value_counts().to_string())
    print(resolution["resolution_hours_ground_truth"].describe().to_string())


if __name__ == "__main__":
    main()
