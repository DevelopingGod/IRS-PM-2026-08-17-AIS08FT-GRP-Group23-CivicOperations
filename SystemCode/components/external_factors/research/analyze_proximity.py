"""Phase 4b - Historical spatial exploration of request-to-facility proximity (train + validation).

Computes nearest-facility distances (projected EPSG:32118, distance to full geometry) for
every geolocated request in train and val, and summarises them by split, borough, facility
category and request type. Test-split proximity is NOT computed here (see holdout_evaluation.py).

Usage: python research/analyze_proximity.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import ALL_CATS, CANDIDATE_RADII, CHARTS, CORE, ROOT, TABLES, cached_distances

import sys
sys.path.insert(0, str(ROOT))
from visualization import style  # noqa: E402

Q = [0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95]


def quantiles(s: pd.Series) -> dict:
    s = s.dropna()
    return {"n": int(len(s)), **{f"p{int(q * 100):02d}": round(float(s.quantile(q)), 1) for q in Q},
            "mean": round(float(s.mean()), 1)}


def main() -> None:
    df = cached_distances(("train", "val"), "trainval")
    v = df[df["geolocation_status"].eq("RESOLVED")].copy()
    tr = v[v["split"].eq("train")]

    rows = []
    for split, g in v.groupby("split"):
        rows.append({"split": split, "group": "ALL", "metric": "d_core", **quantiles(g["d_core"])})
        for b, gb in g.groupby("borough"):
            rows.append({"split": split, "group": b, "metric": "d_core", **quantiles(gb["d_core"])})
        for cat in ALL_CATS:
            rows.append({"split": split, "group": "ALL", "metric": f"d_{cat}", **quantiles(g[f"d_{cat}"])})
    pd.DataFrame(rows).to_csv(TABLES / "nearest_distance_quantiles.csv", index=False)

    # Category x borough median nearest distance (train).
    med = tr.groupby("borough")[[f"d_{c}" for c in ALL_CATS]].median().round(0)
    med.columns = ALL_CATS
    med.to_csv(TABLES / "median_nearest_distance_category_borough_train.csv")

    # Which category is nearest (train)?
    nc = tr["nearest_core_category"].value_counts(normalize=True).mul(100).round(2)
    nc.rename("share_pct").to_csv(TABLES / "nearest_core_category_share_train.csv")

    # By request type (train): median nearest core distance and share within 100/250 m.
    rt = tr.groupby(["request_type_code", "request_type_label", "complaint_family"])["d_core"].agg(
        n="size", median_m="median", p25_m=lambda s: s.quantile(.25),
        within_100m_pct=lambda s: 100 * (s <= 100).mean(), within_250m_pct=lambda s: 100 * (s <= 250).mean())
    rt.round(1).sort_values("median_m").to_csv(TABLES / "proximity_by_request_type_train.csv")
    fam = tr.groupby("complaint_family")["d_core"].agg(n="size", median_m="median",
                                                       within_250m_pct=lambda s: 100 * (s <= 250).mean())
    fam.round(1).sort_values("median_m").to_csv(TABLES / "proximity_by_family_train.csv")

    # Location precision vs distance (train).
    lp = tr.groupby("location_precision")["d_core"].agg(n="size", median_m="median").round(1)
    lp.to_csv(TABLES / "proximity_by_precision_train.csv")

    # Train vs val distribution comparison (two-sample KS on nearest core distance, per borough).
    from scipy.stats import ks_2samp
    ks = []
    for b in style.BOROUGH_ORDER:
        a = v[(v.split == "train") & (v.borough == b)]["d_core"]
        c = v[(v.split == "val") & (v.borough == b)]["d_core"]
        r = ks_2samp(a, c)
        ks.append({"borough": b, "n_train": len(a), "n_val": len(c), "ks_stat": round(float(r.statistic), 4),
                   "p_value": float(r.pvalue), "median_train": round(float(a.median()), 1),
                   "median_val": round(float(c.median()), 1)})
    pd.DataFrame(ks).to_csv(TABLES / "train_val_distance_shift.csv", index=False)

    # ---------------------------------------------------------------- charts
    style.apply()
    import matplotlib.pyplot as plt
    xs = np.linspace(0, 1500, 301)

    fig, ax = plt.subplots(figsize=(8, 4.4))
    for b in style.BOROUGH_ORDER:
        d = np.sort(tr[tr.borough == b]["d_core"].to_numpy())
        y = np.searchsorted(d, xs, side="right") / len(d) * 100
        ax.plot(xs, y, color=style.BOROUGH_COLOR[b], label=b.title())
        ax.annotate(b.title(), (xs[-1], y[-1]), xytext=(4, 0), textcoords="offset points",
                    color=style.INK_2, fontsize=8, va="center")
    for r in (100, 250, 500):
        ax.axvline(r, color=style.AXIS, lw=0.8, ls=(0, (3, 3)))
        ax.text(r, 101, f"{r} m", color=style.MUTED, fontsize=8, ha="center", va="bottom")
    ax.set_xlim(0, 1500)
    ax.set_ylim(0, 100)
    ax.set_xlabel("Straight-line distance to nearest core critical facility (m)")
    ax.set_ylabel("Requests within distance (%)")
    ax.set_title("Cumulative share of requests by nearest-facility distance (train)", pad=16)
    ax.legend(loc="lower right")
    style.save(fig, CHARTS / "ecdf_nearest_core_by_borough.png")

    fig, axes = plt.subplots(2, 4, figsize=(11, 5.2), sharex=True, sharey=True)
    for ax, cat in zip(axes.flat, ALL_CATS):
        for b in style.BOROUGH_ORDER:
            d = np.sort(tr[tr.borough == b][f"d_{cat}"].to_numpy())
            ax.plot(xs, np.searchsorted(d, xs, side="right") / len(d) * 100, color=style.BOROUGH_COLOR[b], lw=1.5)
        ax.set_title(style.CATEGORY_LABEL[cat].replace(" (exploratory)", "*"), fontsize=9.5)
        ax.set_xlim(0, 1500)
        ax.set_ylim(0, 100)
    ax = axes.flat[-1]
    ax.axis("off")
    for b in style.BOROUGH_ORDER:
        ax.plot([], [], color=style.BOROUGH_COLOR[b], label=b.title())
    ax.legend(loc="center", title="Borough")
    fig.supxlabel("Distance to nearest facility of the category (m)", color=style.INK_2, fontsize=10)
    fig.supylabel("Requests within distance (%)", color=style.INK_2, fontsize=10)
    fig.suptitle("Per-category nearest-facility distance by borough (train)   * exploratory category",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    style.save(fig, CHARTS / "ecdf_nearest_by_category.png")

    fig, ax = plt.subplots(figsize=(8, 6.5))
    r = pd.read_csv(TABLES / "proximity_by_request_type_train.csv").sort_values("median_m", ascending=False)
    ax.barh(r["request_type_code"] + "  " + r["request_type_label"].str.slice(0, 34), r["median_m"],
            color=style.SERIES[0], height=0.62)
    ax.set_xlabel("Median distance to nearest core critical facility (m)")
    ax.set_title("Median nearest-facility distance by request type (train)")
    ax.grid(axis="y", visible=False)
    ax.tick_params(axis="y", labelsize=8)
    style.save(fig, CHARTS / "median_distance_by_request_type.png")

    out = {"train_requests_geolocated": int(len(tr)), "val_requests_geolocated": int((v.split == "val").sum()),
           "train_quantiles_d_core": quantiles(tr["d_core"]),
           "train_within_radius_pct": {r: round(float(100 * (tr["d_core"] <= r).mean()), 2) for r in CANDIDATE_RADII},
           "nearest_core_category_share_train": nc.to_dict(),
           "requests_inside_a_facility_polygon_train_pct": round(float(100 * (tr["d_core"] == 0).mean()), 2)}
    (TABLES / "proximity_summary_train.json").write_text(json.dumps(out, indent=2), "utf-8")
    print(json.dumps(out, indent=2))
    print(med)
    print(pd.DataFrame(ks))


if __name__ == "__main__":
    main()
