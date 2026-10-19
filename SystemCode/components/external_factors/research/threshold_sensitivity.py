"""Phase 5a - Threshold sensitivity on the TRAIN split (exploration / candidate development).

For each candidate radius:
  * trigger rate (share of geolocated requests flagged), overall / per borough / per category
  * marginal requests added per step and local elasticity of the trigger rate
  * robustness to location error: every request is displaced by a fixed distance u in a random
    direction (seeded), the decision is recomputed, and the overall flip rate and the flip rate
    relative to flagged requests are reported
  * share of requests in the uncertainty band |threshold - distance| < u for the documented
    default uncertainty of each precision class (the ROBUST_INTERVAL indeterminate share)
  * core categories versus core + exploratory transit stations

No validation or test proximity results are used here.

Usage: python research/threshold_sensitivity.py [--split train]
"""

from __future__ import annotations

import argparse
import json

import numpy as np
import pandas as pd

from common import ALL_CATS, CANDIDATE_RADII, CHARTS, CORE, ROOT, TABLES, build_engine, cached_distances

import sys
sys.path.insert(0, str(ROOT))
from visualization import style  # noqa: E402

JITTERS_M = [15, 25, 50, 120]
DEFAULT_U = {"ADDRESS_POINT": 15, "INTERSECTION": 25, "BLOCKFACE": 120, "PLACE": 150, "UNKNOWN": 100}
SEED = 20261015


def jittered_core_distance(df: pd.DataFrame, u: float, engine, seed: int, cats=CORE) -> np.ndarray:
    """Nearest core distance after displacing each request by exactly u metres in a random direction."""
    rng = np.random.default_rng(seed)
    pts = engine.project(df["latitude"].to_numpy(), df["longitude"].to_numpy())
    import shapely
    x, y = shapely.get_x(pts), shapely.get_y(pts)
    theta = rng.uniform(0, 2 * np.pi, len(df))
    moved = shapely.points(x + u * np.cos(theta), y + u * np.sin(theta))
    mats = np.column_stack([engine.nearest(moved, c)[1] for c in cats])
    return mats.min(axis=1)


def robustness_table(v: pd.DataFrame, engine, radii, jitters=JITTERS_M, reps=2, seed=SEED) -> pd.DataFrame:
    rows = []
    d0 = v["d_core"].to_numpy()
    for u in jitters:
        dj = [jittered_core_distance(v, u, engine, seed + 97 * k) for k in range(reps)]
        for t in radii:
            f0 = d0 <= t
            flips = np.mean([np.mean(f0 != (d <= t)) for d in dj])
            rows.append({"jitter_m": u, "threshold_m": t, "trigger_rate_pct": 100 * f0.mean(),
                         "flip_rate_pct": 100 * flips,
                         "relative_flip_pct": 100 * flips / max(f0.mean(), 1e-9),
                         "boundary_band_pct": 100 * np.mean(np.abs(t - d0) < u)})
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="train", choices=["train"])
    args = ap.parse_args()
    df = cached_distances(("train", "val"), "trainval")
    v = df[df["split"].eq(args.split) & df["geolocation_status"].eq("RESOLVED")].reset_index(drop=True)
    n = len(v)
    engine = build_engine(CORE)

    # 1. Trigger-rate curves.
    curve = []
    for t in CANDIDATE_RADII:
        row = {"threshold_m": t, "n": n,
               "core_flagged": int((v["d_core"] <= t).sum()),
               "core_rate_pct": 100 * (v["d_core"] <= t).mean(),
               "core_plus_transit_rate_pct": 100 * ((v["d_core"] <= t) | (v["d_TRANSIT_STATION"] <= t)).mean()}
        for c in ALL_CATS:
            row[f"{c}_only_rate_pct"] = 100 * (v[f"d_{c}"] <= t).mean()
        for b in style.BOROUGH_ORDER:
            row[f"{b}_rate_pct"] = 100 * (v.loc[v.borough == b, "d_core"] <= t).mean()
        curve.append(row)
    curve = pd.DataFrame(curve)
    curve["marginal_flagged"] = curve["core_flagged"].diff()
    curve["marginal_pct_points_per_100m"] = curve["core_rate_pct"].diff() / curve["threshold_m"].diff() * 100
    rates = curve["core_rate_pct"].to_numpy()
    radii = curve["threshold_m"].to_numpy(dtype=float)
    el = np.full(len(radii), np.nan)
    for i in range(1, len(radii) - 1):
        el[i] = ((rates[i + 1] - rates[i - 1]) / (radii[i + 1] - radii[i - 1])) * radii[i] / rates[i]
    curve["elasticity"] = el
    bcols = [f"{b}_rate_pct" for b in style.BOROUGH_ORDER]
    curve["borough_max_min_ratio"] = curve[bcols].max(axis=1) / curve[bcols].min(axis=1)
    curve.round(3).to_csv(TABLES / "trigger_rate_curve_train.csv", index=False)

    # 2. Robustness to location error.
    rob = robustness_table(v, engine, CANDIDATE_RADII)
    rob.round(3).to_csv(TABLES / "uncertainty_robustness_train.csv", index=False)

    # 3. Indeterminate share in ROBUST_INTERVAL regime, per precision class (default uncertainty).
    ind = []
    for p, g in v.groupby("location_precision"):
        u = DEFAULT_U.get(p, 100)
        for t in CANDIDATE_RADII:
            m = t - g["d_core"]
            ind.append({"precision": p, "n": len(g), "default_uncertainty_m": u, "threshold_m": t,
                        "band_pct": 100 * (m.abs() < u).mean(),
                        "robust_positive_pct": 100 * (m >= u).mean(), "robust_negative_pct": 100 * (m < -u).mean()})
    pd.DataFrame(ind).round(3).to_csv(TABLES / "precision_band_train.csv", index=False)

    # 4. Category contribution: which category is the ONLY trigger at each radius.
    contrib = []
    for t in CANDIDATE_RADII:
        hit = pd.DataFrame({c: v[f"d_{c}"] <= t for c in CORE})
        any_hit = hit.any(axis=1)
        for c in CORE:
            contrib.append({"threshold_m": t, "category": c, "rate_pct": 100 * hit[c].mean(),
                            "sole_trigger_pct_of_flagged": 100 * (hit[c] & (hit.sum(axis=1) == 1)).sum() / max(any_hit.sum(), 1),
                            "removing_category_drops_rate_pp": 100 * (any_hit.mean() - hit.drop(columns=c).any(axis=1).mean())})
    pd.DataFrame(contrib).round(3).to_csv(TABLES / "category_contribution_train.csv", index=False)

    # ---------------------------------------------------------------- charts
    style.apply()
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(8, 4.4))
    ax.plot(curve.threshold_m, curve.core_rate_pct, color=style.SERIES[0], marker="o", ms=5, label="Core categories (all boroughs)")
    ax.plot(curve.threshold_m, curve.core_plus_transit_rate_pct, color=style.SERIES[1], marker="o", ms=5,
            label="Core + transit stations (exploratory)")
    for _, r in curve[curve.threshold_m.isin([100, 250, 500])].iterrows():
        ax.annotate(f"{r.core_rate_pct:.0f}%", (r.threshold_m, r.core_rate_pct), xytext=(6, -12),
                    textcoords="offset points", fontsize=8, color=style.INK_2)
    ax.set_xlabel("Candidate threshold (m)")
    ax.set_ylabel("Requests flagged (%)")
    ax.set_ylim(0, 100)
    ax.set_xlim(0, 1020)
    ax.set_title("Trigger-rate curve by candidate threshold (train)")
    ax.legend(loc="lower right")
    style.save(fig, CHARTS / "trigger_rate_curve.png")

    fig, ax = plt.subplots(figsize=(8, 4.4))
    for b in style.BOROUGH_ORDER:
        ax.plot(curve.threshold_m, curve[f"{b}_rate_pct"], color=style.BOROUGH_COLOR[b], marker="o", ms=4, label=b.title())
    ax.set_xlim(0, 520)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Candidate threshold (m)")
    ax.set_ylabel("Requests flagged (%)")
    ax.set_title("Trigger rate by borough (train, core categories)")
    ax.legend(loc="lower right")
    style.save(fig, CHARTS / "trigger_rate_by_borough.png")

    fig, ax = plt.subplots(figsize=(8, 4.4))
    for c in ALL_CATS:
        ax.plot(curve.threshold_m, curve[f"{c}_only_rate_pct"], color=style.CATEGORY_COLOR[c],
                marker=style.CATEGORY_MARKER[c], ms=5, label=style.CATEGORY_LABEL[c],
                ls=(0, (4, 2)) if c == "TRANSIT_STATION" else "-")
    ax.set_xlim(0, 1020)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Candidate threshold (m)")
    ax.set_ylabel("Requests flagged by this category alone (%)")
    ax.set_title("Single-category trigger rate (train)")
    ax.legend(loc="upper left", fontsize=8)
    style.save(fig, CHARTS / "trigger_rate_by_category.png")

    fig, axes = plt.subplots(1, 2, figsize=(11, 4.2))
    for k, u in enumerate(JITTERS_M):
        r = rob[rob.jitter_m == u]
        axes[0].plot(r.threshold_m, r.flip_rate_pct, color=style.SERIES[k], marker="o", ms=4, label=f"{u} m displacement")
        axes[1].plot(r.threshold_m, r.relative_flip_pct, color=style.SERIES[k], marker="o", ms=4, label=f"{u} m")
    axes[0].set_title("Decisions that change (% of all requests)")
    axes[1].set_title("Changes relative to flagged requests (%)")
    for ax in axes:
        ax.set_xlabel("Candidate threshold (m)")
        ax.set_xlim(0, 1020)
    axes[1].set_yscale("log")
    axes[0].legend(loc="upper right", title="Location error")
    fig.suptitle("Sensitivity of the binary decision to location error (train)", x=0.01, ha="left",
                 fontsize=12, fontweight="bold")
    fig.tight_layout()
    style.save(fig, CHARTS / "uncertainty_flip_rates.png")

    fig, ax = plt.subplots(figsize=(8, 3.8))
    c2 = curve.dropna(subset=["marginal_pct_points_per_100m"])
    ax.bar(c2.threshold_m.astype(int).astype(str), c2.marginal_pct_points_per_100m, color=style.SERIES[0], width=0.6)
    ax.set_xlabel("Candidate threshold (m), step from previous candidate")
    ax.set_ylabel("Added flagged share\n(pp per 100 m)")
    ax.set_title("Marginal trigger-rate gain per 100 m (train)")
    ax.grid(axis="x", visible=False)
    style.save(fig, CHARTS / "marginal_gain.png")

    show = curve[["threshold_m", "core_rate_pct", "marginal_flagged", "elasticity", "borough_max_min_ratio"]]
    print(show.round(2).to_string(index=False))
    print(rob[rob.jitter_m.isin([15, 25])].round(2).to_string(index=False))
    print(pd.DataFrame(contrib).round(2).query("threshold_m in [75,100,150]").to_string(index=False))
    summary = {"split": args.split, "n": n, "candidate_radii_m": CANDIDATE_RADII, "jitters_m": JITTERS_M, "seed": SEED}
    (TABLES / "threshold_sensitivity_meta.json").write_text(json.dumps(summary, indent=2), "utf-8")


if __name__ == "__main__":
    main()
