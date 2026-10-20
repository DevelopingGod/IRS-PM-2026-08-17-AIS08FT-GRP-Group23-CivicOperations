"""Phase 8b - Hold-out evaluation of the FROZEN configuration, plus decision behaviour on all splits.

Runs after config/proximity_rules.yaml has been written by select_thresholds.py. Nothing here
feeds back into threshold choice. The full evaluator (validation, precision regimes, evidence,
explanations) is run on every historical request. The test split is compared with validation
against the pre-declared hold-out tolerance (|trigger-rate shift| <= 5 pp).

There are no outcome labels, so no accuracy/precision/recall/F1 is reported. A trigger rate
is a description of the flag, not a prediction quality.

Usage: python research/holdout_evaluation.py
"""

from __future__ import annotations

import json
import time

import numpy as np
import pandas as pd
import yaml

from common import CHARTS, CORE, ROOT, TABLES, build_engine, cached_distances, load_requests
from threshold_sensitivity import jittered_core_distance

import sys
sys.path.insert(0, str(ROOT))
from civicops_external_factors.evaluator import CriticalFacilityEvaluator  # noqa: E402
from visualization import style  # noqa: E402

SEED = 20261015


def to_contexts(df: pd.DataFrame) -> list[dict]:
    out = []
    for r in df.itertuples(index=False):
        out.append({"request_id": r.request_id, "request_type": r.request_type_code,
                    "latitude": None if pd.isna(r.latitude) else float(r.latitude),
                    "longitude": None if pd.isna(r.longitude) else float(r.longitude),
                    "geolocation_status": r.geolocation_status, "location_precision": r.location_precision,
                    "borough": r.borough, "location_type": None if pd.isna(r.location_type) else r.location_type,
                    "location_source": "NYC311_HISTORICAL"})
    return out


def main() -> None:
    cfg = yaml.safe_load(open(ROOT / "config" / "proximity_rules.yaml", encoding="utf-8"))
    proto = yaml.safe_load(open(ROOT / "config" / "threshold_selection_protocol.yaml", encoding="utf-8"))
    t = int(cfg["threshold_selection"]["selected_threshold_m"])
    ev = CriticalFacilityEvaluator.from_config(ROOT / "config" / "proximity_rules.yaml")

    # --- full evaluator on all splits (decision behaviour)
    df = load_requests()
    t0 = time.perf_counter()
    results = ev.evaluate_batch(to_contexts(df))
    elapsed = time.perf_counter() - t0
    rec = pd.DataFrame([{**{k: r.to_dict()[k] for k in ("request_id", "external_factor", "evaluation_status",
                                                         "incomplete_reason", "triggering_facility_count",
                                                         "nearest_qualifying_distance_m", "explanation")},
                         "regime": r.location_quality.decision_regime,
                         "boundary_sensitive": r.location_quality.boundary_sensitive,
                         "categories": ",".join(sorted({f.facility_category for f in r.triggering_facilities})),
                         "rules": ",".join(r.triggered_rules), "n_warnings": len(r.warnings),
                         "borough_mismatch": any(w.startswith("BOROUGH_MISMATCH") for w in r.warnings)}
                        for r in results])
    rec = df[["request_id", "split", "borough", "request_type_code", "location_precision"]].merge(rec, on="request_id")
    rec.to_parquet(TABLES / "historical_decisions.parquet", index=False)

    beh = []
    for split, g in list(rec.groupby("split")) + [("all", rec)]:
        n = len(g)
        beh.append({"split": split, "n": n,
                    "flagged_1": int((g.external_factor == 1).sum()), "not_flagged_0": int((g.external_factor == 0).sum()),
                    "indeterminate_null": int(g.external_factor.isna().sum()),
                    "flagged_pct": 100 * (g.external_factor == 1).mean(), "not_flagged_pct": 100 * (g.external_factor == 0).mean(),
                    "indeterminate_pct": 100 * g.external_factor.isna().mean(),
                    "robust_interval_regime_pct": 100 * (g.regime == "ROBUST_INTERVAL").mean(),
                    "boundary_sensitive_pct_of_complete": 100 * g.loc[g.external_factor.notna(), "boundary_sensitive"].astype(bool).mean()})
    beh = pd.DataFrame(beh)
    beh.round(3).to_csv(TABLES / "decision_behaviour_by_split.csv", index=False)
    reasons = rec[rec.external_factor.isna()].groupby(["split", "incomplete_reason"]).size().unstack(0, fill_value=0)
    reasons.to_csv(TABLES / "indeterminate_reasons_by_split.csv")
    prec = rec.groupby(["location_precision"]).agg(n=("request_id", "size"),
                                                   flagged_pct=("external_factor", lambda s: 100 * (s == 1).mean()),
                                                   indeterminate_pct=("external_factor", lambda s: 100 * s.isna().mean()))
    prec.round(2).to_csv(TABLES / "decision_by_precision.csv")
    cat_counts = (rec.loc[rec.external_factor == 1, "categories"].str.split(",").explode().value_counts())
    cat_df = pd.DataFrame({"requests_with_category_triggering": cat_counts,
                           "pct_of_flagged": 100 * cat_counts / (rec.external_factor == 1).sum()})
    cat_df.round(2).to_csv(TABLES / "triggering_categories_all_splits.csv")
    rule_counts = rec["rules"].str.split(",").explode().value_counts()
    rule_counts.rename("count").to_csv(TABLES / "rule_frequency_all_splits.csv")
    expl = {
        "results": int(len(rec)),
        "explanation_non_empty_pct": 100 * rec.explanation.str.len().gt(20).mean(),
        "triggered_rules_non_empty_pct": 100 * rec.rules.str.len().gt(0).mean(),
        "positives_with_facility_evidence_pct": 100 * rec.loc[rec.external_factor == 1, "triggering_facility_count"].gt(0).mean(),
        "complete_with_nearest_qualifying_distance_pct": 100 * rec.loc[rec.external_factor.notna(), "nearest_qualifying_distance_m"].notna().mean(),
        "borough_mismatch_warnings": int(rec.borough_mismatch.sum()),
        "batch_seconds": round(elapsed, 2), "batch_requests_per_second": round(len(rec) / elapsed, 1),
    }

    # --- hold-out: point-estimate trigger rate etc. on test vs val (frozen threshold)
    test = cached_distances(("test",), "test")
    test = test[test.geolocation_status.eq("RESOLVED")].reset_index(drop=True)
    tv = cached_distances(("train", "val"), "trainval")
    tv = tv[tv.geolocation_status.eq("RESOLVED")]
    engine = build_engine(CORE)
    rows = []
    for split, g in [("train", tv[tv.split == "train"].reset_index(drop=True)),
                     ("val", tv[tv.split == "val"].reset_index(drop=True)), ("test", test)]:
        f0 = (g.d_core <= t).to_numpy()
        dj = [jittered_core_distance(g, proto["criteria"]["displacement_m"], engine, SEED + 97 * k) for k in range(2)]
        agree = 100 * (1 - np.mean([np.mean(f0 != (d <= t)) for d in dj]))
        pos = 100 * (1 - np.mean([np.mean(d[f0] > t) for d in dj]))
        b = {bb: 100 * (g.loc[g.borough == bb, "d_core"] <= t).mean() for bb in style.BOROUGH_ORDER}
        rows.append({"split": split, "n_geolocated": len(g), "trigger_rate_pct": 100 * f0.mean(),
                     "median_nearest_core_m": float(g.d_core.median()), "decision_agreement_15m_pct": agree,
                     "positive_stability_15m_pct": pos, "borough_max_min_ratio": max(b.values()) / min(b.values()),
                     **{f"{bb}_rate_pct": v for bb, v in b.items()}})
    hold = pd.DataFrame(rows)
    hold.round(3).to_csv(TABLES / "holdout_comparison.csv", index=False)
    shift = float(hold.set_index("split").loc["test", "trigger_rate_pct"] - hold.set_index("split").loc["val", "trigger_rate_pct"])
    crit = proto["criteria"]
    hs = hold.set_index("split").loc["test"]
    holdout = {"selected_threshold_m": t, "test_minus_val_trigger_rate_pp": shift,
               "holdout_tolerance_pp": crit["C3_temporal_stability_max_abs_pp"],
               "holdout_shift_within_tolerance": abs(shift) <= crit["C3_temporal_stability_max_abs_pp"],
               "test_C1_decision_agreement": bool(hs.decision_agreement_15m_pct >= crit["C1_decision_agreement_min_pct"]),
               "test_C2_positive_stability": bool(hs.positive_stability_15m_pct >= crit["C2_positive_stability_min_pct"]),
               "test_C4_informativeness": bool(hs.trigger_rate_pct <= crit["C4_informativeness_max_trigger_pct"]),
               "test_C5_borough_ratio": bool(hs.borough_max_min_ratio <= crit["C5_borough_max_min_ratio_max"]),
               "note": "Descriptive hold-out check of the frozen configuration; no thresholds were changed after it."}
    out = {"explanation_completeness": expl, "holdout": holdout}
    (TABLES / "holdout_summary.json").write_text(json.dumps(out, indent=2, default=float), "utf-8")

    style.apply()
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(8, 3.8))
    x = np.arange(len(style.BOROUGH_ORDER))
    for k, s in enumerate(["train", "val", "test"]):
        r = hold.set_index("split").loc[s]
        ax.bar(x + (k - 1) * 0.26, [r[f"{b}_rate_pct"] for b in style.BOROUGH_ORDER], 0.23,
               color=style.SPLIT_COLOR[s], label=f"{s} ({r.trigger_rate_pct:.1f}% overall)")
    ax.set_xticks(x, [b.title() for b in style.BOROUGH_ORDER])
    ax.set_ylabel("Requests flagged (%)")
    ax.set_title(f"Trigger rate at the frozen {t} m threshold by borough and split")
    ax.grid(axis="x", visible=False)
    ax.legend(loc="upper right")
    style.save(fig, CHARTS / "holdout_trigger_rate_by_borough.png")

    fig, ax = plt.subplots(figsize=(8, 3.6))
    b2 = beh[beh.split != "all"].set_index("split").loc[["train", "val", "test"]]
    left = np.zeros(3)
    for col, lab, colr in [("flagged_pct", "1 - facility within threshold", style.SERIES[0]),
                           ("not_flagged_pct", "0 - none within threshold", "#b9b8b2"),
                           ("indeterminate_pct", "null - incomplete", style.SERIES[1])]:
        ax.barh(b2.index, b2[col], left=left, color=colr, label=lab, height=0.55, edgecolor=style.SURFACE, linewidth=2)
        for i, (l, w) in enumerate(zip(left, b2[col])):
            if w > 4:
                ax.text(l + w / 2, i, f"{w:.1f}%", ha="center", va="center", color="white" if colr != "#b9b8b2" else style.INK, fontsize=8.5)
        left += b2[col].to_numpy()
    ax.set_xlim(0, 100)
    ax.invert_yaxis()
    ax.set_xlabel("Share of historical requests (%)")
    ax.set_title("Decision behaviour of the full evaluator by split")
    ax.grid(axis="y", visible=False)
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.25), ncols=3)
    style.save(fig, CHARTS / "decision_behaviour_by_split.png")

    print(beh.round(2).to_string(index=False))
    print(reasons)
    print(prec.round(2))
    print(cat_df.round(2))
    print(hold.round(2).to_string(index=False))
    print(json.dumps(out, indent=2, default=float))


if __name__ == "__main__":
    main()
