"""Phase 5b/6 - Apply the pre-declared selection protocol on the VALIDATION split.

Reads config/threshold_selection_protocol.yaml (fixed before validation results existed),
evaluates every candidate radius against criteria C1-C5 on validation, applies the decision
rule, compares alternative configurations, and (with --write-config) writes the frozen
config/proximity_rules.yaml. Test-split data are not read.

Usage: python research/select_thresholds.py --write-config
"""

from __future__ import annotations

import argparse
import json
from datetime import date

import numpy as np
import pandas as pd
import yaml

from common import CHARTS, CORE, ROOT, TABLES, build_engine, cached_distances
from threshold_sensitivity import jittered_core_distance

import sys
sys.path.insert(0, str(ROOT))
from visualization import style  # noqa: E402

PROTOCOL = ROOT / "config" / "threshold_selection_protocol.yaml"
INTERIOR_TYPES = ["RT080", "RT081", "RT082", "RT083", "RT084", "RT141"]
SEED = 20261015


def rate(mask) -> float:
    return float(100 * np.mean(mask))


def evaluate_candidates(val: pd.DataFrame, train: pd.DataFrame, proto: dict, engine) -> pd.DataFrame:
    crit = proto["criteria"]
    u = crit["displacement_m"]
    dj = [jittered_core_distance(val, u, engine, SEED + 97 * k) for k in range(2)]
    d0 = val["d_core"].to_numpy()
    lo, hi = proto["admissible_range_m"]
    rows = []
    for t in proto["candidate_radii_m"]:
        f0 = d0 <= t
        flips = np.mean([np.mean(f0 != (d <= t)) for d in dj])
        pos_flips = np.mean([np.mean((d[f0] > t)) for d in dj]) if f0.any() else 0.0
        b_rates = {b: rate(val.loc[val.borough == b, "d_core"] <= t) for b in style.BOROUGH_ORDER}
        r_val, r_train = rate(f0), rate(train["d_core"] <= t)
        uniq = val.drop_duplicates(["latitude", "longitude"])
        row = {"threshold_m": t, "admissible_H1": lo <= t <= hi,
               "trigger_rate_val_pct": r_val, "trigger_rate_train_pct": r_train,
               "trigger_rate_val_unique_locations_pct": rate(uniq["d_core"] <= t),
               "decision_agreement_pct": 100 * (1 - flips), "positive_stability_pct": 100 * (1 - pos_flips),
               "temporal_shift_pp": r_val - r_train,
               "borough_max_min_ratio": max(b_rates.values()) / max(min(b_rates.values()), 1e-9),
               **{f"{b}_rate_val_pct": r for b, r in b_rates.items()}}
        row["C1"] = row["decision_agreement_pct"] >= crit["C1_decision_agreement_min_pct"]
        row["C2"] = row["positive_stability_pct"] >= crit["C2_positive_stability_min_pct"]
        row["C3"] = abs(row["temporal_shift_pp"]) <= crit["C3_temporal_stability_max_abs_pp"]
        row["C4"] = row["trigger_rate_val_pct"] <= crit["C4_informativeness_max_trigger_pct"]
        row["C5"] = row["borough_max_min_ratio"] <= crit["C5_borough_max_min_ratio_max"]
        row["meets_all"] = all(row[c] for c in ("C1", "C2", "C3", "C4", "C5"))
        rows.append(row)
    return pd.DataFrame(rows)


def apply_rule(tab: pd.DataFrame, proto: dict) -> tuple[int, str, list]:
    prior = proto["knowledge_based_prior_m"]
    r = tab.set_index("threshold_m")
    if r.loc[prior, "admissible_H1"] and r.loc[prior, "meets_all"]:
        return prior, "knowledge-based prior meets all validation criteria", []
    ok = tab[tab.admissible_H1 & tab.meets_all]
    failed = [c for c in ("C1", "C2", "C3", "C4", "C5") if not r.loc[prior, c]]
    if len(ok):
        t = int(ok.threshold_m.min())
        return t, f"prior failed {failed}; smallest admissible candidate meeting all criteria", failed
    return prior, f"no admissible candidate meets all criteria; prior kept, failed {failed} documented", failed


def compare_configurations(val: pd.DataFrame, t: int, proto: dict) -> pd.DataFrame:
    radii = proto["candidate_radii_m"]
    t_next = next((r for r in radii if r > t), t)
    universal = (val["d_core"] <= t)
    vuln = ["SCHOOL", "CHILDCARE", "SENIOR_CARE"]
    emerg = ["HOSPITAL", "FIRE_STATION", "POLICE"]
    cat_specific = np.zeros(len(val), bool)
    for c in vuln:
        cat_specific |= (val[f"d_{c}"] <= t).to_numpy()
    for c in emerg:
        cat_specific |= (val[f"d_{c}"] <= t_next).to_numpy()
    transit = universal | (val["d_TRANSIT_STATION"] <= t)
    interior = val["request_type_code"].isin(INTERIOR_TYPES).to_numpy()
    context = universal.to_numpy() & ~interior
    rows = []
    for name, flag, desc, n_rules, uses_rt in [
        ("A_universal_pure_geographic", universal.to_numpy(), f"all core categories <= {t} m", 6, False),
        ("B1_category_specific", cat_specific, f"vulnerable sites <= {t} m, emergency sites <= {t_next} m", 6, False),
        ("B2_core_plus_transit", transit.to_numpy(), f"core + transit stations <= {t} m", 7, False),
        ("B3_context_aware_interior_gating", context, f"A, but 0 for {', '.join(INTERIOR_TYPES)}", 7, True)]:
        rows.append({"configuration": name, "definition": desc, "trigger_rate_val_pct": rate(flag),
                     "agreement_with_A_pct": rate(flag == universal.to_numpy()),
                     "flags_added_vs_A": int((flag & ~universal.to_numpy()).sum()),
                     "flags_removed_vs_A": int((~flag & universal.to_numpy()).sum()),
                     "n_rules": n_rules, "uses_request_type": uses_rt,
                     "double_counting_risk_with_urgency": "yes - request type also drives Urgency" if uses_rt else "low"})
    out = pd.DataFrame(rows)
    out.attrs["interior_share_of_A_positives_pct"] = rate(interior[universal.to_numpy()])
    return out


RULESET_TEMPLATE = """# CivicOps AI - External Factors: Critical Facility Proximity ruleset
# GENERATED by research/select_thresholds.py on {today} from validation-split evidence under
# config/threshold_selection_protocol.yaml. Edit thresholds here; no source-code change is needed.
#
# The threshold is a knowledge-based heuristic (operational definition of "adjacent") checked
# against pre-declared validation criteria. It is NOT statistically optimal and NOT an official
# NYC policy value; no independent outcome labels exist.

ruleset_version: "1.0.0"

# PURE_GEOGRAPHIC (default): external_factor == critical_facility_nearby; request type is never used.
# CONTEXT_AWARE (opt-in, needs agreement with the Urgency owner): factor forced to 0 for the
#   building-interior request types listed under context_aware.
decision_mode: PURE_GEOGRAPHIC

metric_crs: "EPSG:32118"            # NAD83 / New York Long Island (metres)
osm_data_dir: "../data/osm"         # relative to this file; override with --osm-data-dir
request_catalog_path: "../data/reference/request_catalog.csv"

# Qualifying facility categories. A request is flagged when ANY qualifying facility's geometry
# lies within that category's threshold (distance <= threshold_m, inclusive).
facility_categories:
{categories}
evidence:
  # Categories reported as "nearest_facility_overall" evidence without qualifying for the factor.
  nearest_overall_categories: [TRANSIT_STATION]
  max_facilities_listed: 10
  include_duplicates: false         # duplicate OSM representations are excluded from evaluation

location:
  accepted_geolocation_status: [RESOLVED, APPROXIMATE]
  precision_aliases: {{ROOFTOP: ADDRESS_POINT, ADDRESS: ADDRESS_POINT, GPS: GPS_DEVICE, STREET_SEGMENT: BLOCKFACE,
                      LANDMARK: PLACE, PLACENAME: PLACE, ZIP: POSTAL_CODE, ZIP_CODE: POSTAL_CODE}}
  # Heuristic default positional uncertainty (m) used when location_uncertainty_m is not supplied.
  default_uncertainty_m: {{ADDRESS_POINT: 15, GPS_DEVICE: 20, INTERSECTION: 25, BLOCKFACE: 120, PLACE: 150, UNKNOWN: 100}}
  unsupported_precision: [POSTAL_CODE, NEIGHBORHOOD, BOROUGH, CITY]
  # uncertainty <= this -> POINT_ESTIMATE decision; above -> ROBUST_INTERVAL (decide only if the
  # whole uncertainty disc is inside / outside the threshold, otherwise null).
  point_decision_max_uncertainty_m: 30
  max_evaluable_uncertainty_m: 250  # above -> INCOMPLETE (INSUFFICIENT_PRECISION)
  request_coverage_tolerance_m: 100

review_boundary_sensitive: false    # true -> needs_review when |margin| < uncertainty

context_aware:
  not_applicable_request_types: [RT080, RT081, RT082, RT083, RT084, RT141]
  rationale: >-
    Building-interior conditions (heat, leaks, plumbing, unsanitary conditions, electrical,
    elevator) do not occur in the public right of way. Disabled by default: request type is
    the Urgency component's domain.

threshold_selection:
{provenance}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-config", action="store_true")
    args = ap.parse_args()
    proto = yaml.safe_load(open(PROTOCOL, encoding="utf-8"))
    df = cached_distances(("train", "val"), "trainval")
    df = df[df["geolocation_status"].eq("RESOLVED")]
    val = df[df.split.eq("val")].reset_index(drop=True)
    train = df[df.split.eq("train")].reset_index(drop=True)
    engine = build_engine(CORE)

    tab = evaluate_candidates(val, train, proto, engine)
    tab.round(3).to_csv(TABLES / "threshold_selection_val.csv", index=False)
    t, reason, failed = apply_rule(tab, proto)
    comp = compare_configurations(val, t, proto)
    comp.round(3).to_csv(TABLES / "configuration_comparison_val.csv", index=False)
    sel = tab.set_index("threshold_m").loc[t]

    result = {
        "protocol_version": proto["protocol_version"], "selected_threshold_m": int(t), "reason": reason,
        "prior_failed_criteria": failed, "configuration": "A_universal_pure_geographic",
        "configuration_reason": ("Simplest configuration. Category-specific radii (B1) have no outcome evidence "
                                 "behind them. Transit (B2) acts as a density proxy. Request-type gating (B3) "
                                 "overlaps with Urgency and needs team agreement."),
        "validation_metrics_at_selected": {k: (float(v) if isinstance(v, (int, float, np.floating, np.integer)) else bool(v))
                                           for k, v in sel.items()},
        "interior_share_of_positives_val_pct": comp.attrs["interior_share_of_A_positives_pct"],
        "n_val": int(len(val)), "n_train": int(len(train)), "evidence_type": "heuristic, empirically checked",
        "date": str(date.today()),
    }
    (TABLES / "threshold_selection.json").write_text(json.dumps(result, indent=2), "utf-8")

    # Selection chart: validation metrics per candidate with criteria bounds.
    style.apply()
    import matplotlib.pyplot as plt
    crit = proto["criteria"]
    fig, axes = plt.subplots(1, 3, figsize=(12, 3.8))
    x = tab.threshold_m
    lo, hi = proto["admissible_range_m"]
    for ax in axes:
        ax.axvspan(lo, hi, color=style.SEQ_BLUE[0], alpha=0.55, lw=0)
        ax.axvline(t, color=style.SERIES[0], lw=1.2, ls=(0, (4, 2)))
        ax.set_xlim(0, 520)
        ax.set_xlabel("Candidate threshold (m)")
    axes[0].plot(x, tab.trigger_rate_val_pct, color=style.SERIES[0], marker="o", ms=4, label="validation")
    axes[0].plot(x, tab.trigger_rate_train_pct, color=style.SERIES[1], marker="o", ms=4, label="train")
    axes[0].axhline(crit["C4_informativeness_max_trigger_pct"], color=style.MUTED, lw=0.9, ls=":")
    axes[0].set_title("Trigger rate (C3, C4)")
    axes[0].set_ylabel("%")
    axes[0].legend(loc="lower right")
    axes[1].plot(x, tab.decision_agreement_pct, color=style.SERIES[0], marker="o", ms=4, label="all decisions (C1)")
    axes[1].plot(x, tab.positive_stability_pct, color=style.SERIES[2], marker="s", ms=4, label="positives (C2)")
    axes[1].axhline(crit["C1_decision_agreement_min_pct"], color=style.SERIES[0], lw=0.8, ls=":")
    axes[1].axhline(crit["C2_positive_stability_min_pct"], color=style.SERIES[2], lw=0.8, ls=":")
    axes[1].set_title(f"Unchanged under {crit['displacement_m']} m location error")
    axes[1].set_ylabel("%")
    axes[1].legend(loc="lower right")
    axes[2].plot(x, tab.borough_max_min_ratio, color=style.SERIES[0], marker="o", ms=4)
    axes[2].axhline(crit["C5_borough_max_min_ratio_max"], color=style.MUTED, lw=0.9, ls=":")
    axes[2].set_title("Borough max/min trigger ratio (C5)")
    fig.suptitle(f"Validation evidence for threshold selection (shaded: admissible range; dashed: selected {t} m)",
                 x=0.01, ha="left", fontsize=12, fontweight="bold")
    fig.tight_layout()
    style.save(fig, CHARTS / "threshold_selection_validation.png")

    if args.write_config:
        cats = "\n".join(f"  {c + ':':<14}{{threshold_m: {t}, rule_id: CRITICAL_PROXIMITY_{c}}}" for c in CORE)
        prov = {"protocol": "config/threshold_selection_protocol.yaml", "selected_threshold_m": int(t),
                "selection_split": "val", "reason": reason,
                "evidence_type": "knowledge-based heuristic, empirically checked (no outcome labels)",
                "val_trigger_rate_pct": round(float(sel.trigger_rate_val_pct), 2),
                "val_decision_agreement_15m_pct": round(float(sel.decision_agreement_pct), 2),
                "val_positive_stability_15m_pct": round(float(sel.positive_stability_pct), 2),
                "train_val_shift_pp": round(float(sel.temporal_shift_pp), 2), "selected_on": str(date.today())}
        prov_yaml = "\n".join("  " + line for line in yaml.safe_dump(prov, sort_keys=False).strip().splitlines())
        text = RULESET_TEMPLATE.format(today=date.today(), categories=cats, provenance=prov_yaml)
        (ROOT / "config" / "proximity_rules.yaml").write_text(text, "utf-8")
        print("wrote config/proximity_rules.yaml")
    cols = ["threshold_m", "admissible_H1", "trigger_rate_val_pct", "trigger_rate_train_pct",
            "trigger_rate_val_unique_locations_pct", "decision_agreement_pct", "positive_stability_pct",
            "temporal_shift_pp", "borough_max_min_ratio", "C1", "C2", "C3", "C4", "C5", "meets_all"]
    print(tab[cols].round(2).to_string(index=False))
    print(comp.round(2).to_string(index=False))
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
