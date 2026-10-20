"""Phase 3 research: evidence-based selection of the routing approach.

Protocol (task section 7 and 8):

* ``train``  - fit every data-derived quantity (majority tables, referral
  rates, pseudo-count choice candidates).
* ``val``    - compare candidates and select configurations.
* ``test``   - touched once, at the end, to report the selected configuration
  and the alternatives side by side. Nothing is re-fitted after looking at it.

Historical records are turned into "perfect intake" CO1 messages: the RT code
is taken from the record and the runtime-available slot ``borough`` is kept.
No other historical column is used as an input, because the CO1 contract does
not carry complaint_type, descriptor, location_type or coordinates.

Candidates evaluated
--------------------
A   Direct catalog lookup (RT code -> agency).
C1  Learned majority agency per RT code (train). A minimal data-mining
    classifier: if history disagreed with the catalog, it would show here.
C2  Learned majority agency per (RT code, borough) (train). Tests whether the
    only extra runtime slot carries routing signal.
C3  "Final agency" majority per RT code, where the final agency is recovered
    from referral closure notes (train). This is the strongest correction
    model the data permits; it is evaluated against the determinable final
    agency, with undeterminable referrals excluded, and its caveats reported.
R   Referral-risk estimators (knowledge discovery, not routing): smoothed
    P(referred | RT) and P(referred | RT, borough). Selected on validation by
    log-loss; the advisory threshold is selected on validation by F1.

Approach B (catalog plus knowledge-based rules) yields the same agency as A on
well-formed inputs by construction; it differs in validation, safety gating
and explanation, which is measured on simulated CO1 scenarios in
``benchmarks/benchmark_agency.py`` rather than on history.

Outputs: comparison tables in ``outputs/research`` and the runtime knowledge
artifact ``data/reference/referral_knowledge.json`` (fitted on train only).

Run:  python research/method_comparison.py
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402
from referral_analysis import classify_destination  # noqa: E402

sys.path.insert(0, str(common.ROOT))
from civicops_agency.catalog import catalog_content_hash  # noqa: E402

PSEUDO_COUNTS_RT = [0.5, 1, 2, 5, 10, 20, 50]
PSEUDO_COUNTS_CELL = [5, 10, 20, 50, 100, 200, 500, 1000]
THRESHOLDS = [0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.40]
MIN_SUPPORT = 30  # train records required before a code's rate is trusted


# ---------------------------------------------------------------------------
# Metric helpers (implemented directly to avoid a scikit-learn dependency).
# ---------------------------------------------------------------------------
def auc(y: np.ndarray, p: np.ndarray) -> float:
    """ROC AUC via the Mann-Whitney U statistic with average ranks for ties."""
    y = np.asarray(y, dtype=bool)
    ranks = pd.Series(p).rank(method="average").to_numpy()
    n_pos, n_neg = y.sum(), (~y).sum()
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[y].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def log_loss(y: np.ndarray, p: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(y: np.ndarray, p: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def flag_metrics(y: np.ndarray, flagged: np.ndarray) -> dict:
    """Precision/recall/F1 for 'advisory raised' against 'request was referred'."""
    tp = int((flagged & y).sum())
    fp = int((flagged & ~y).sum())
    fn = int((~flagged & y).sum())
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"flag_rate": round(float(flagged.mean()), 4), "precision": round(prec, 4),
            "recall": round(rec, 4), "f1": round(f1, 4), "flagged": int(flagged.sum()), "tp": tp}


# ---------------------------------------------------------------------------
# Referral-risk estimators fitted on train.
# ---------------------------------------------------------------------------
def fit_rt_rates(train: pd.DataFrame, m: float) -> tuple[dict, float]:
    """m-estimate of P(referred | RT) shrunk toward the global train rate."""
    prior = float(train["referred"].mean())
    g = train.groupby("request_type_code")["referred"].agg(["sum", "size"])
    rates = ((g["sum"] + m * prior) / (g["size"] + m)).to_dict()
    return rates, prior


def predict_rt(df: pd.DataFrame, rates: dict, prior: float) -> np.ndarray:
    return df["request_type_code"].map(rates).fillna(prior).to_numpy(dtype=float)


def fit_rt_borough(train: pd.DataFrame, m: float, rt_rates: dict, prior: float) -> dict:
    """Two-level m-estimate: (RT, borough) cell shrunk toward its RT rate."""
    g = train.groupby(["request_type_code", "borough"])["referred"].agg(["sum", "size"])
    out = {}
    for (code, borough), row in g.iterrows():
        base = rt_rates.get(code, prior)
        out[(code, borough)] = (row["sum"] + m * base) / (row["size"] + m)
    return out


def predict_rt_borough(df: pd.DataFrame, cell: dict, rt_rates: dict, prior: float) -> np.ndarray:
    return np.array([cell.get((c, b), rt_rates.get(c, prior))
                     for c, b in zip(df["request_type_code"], df["borough"])], dtype=float)


def main() -> None:
    df = common.load_history()
    cat = common.load_catalog()
    mmap = common.load_matrix_map().set_index("request_type_code")
    cat_agency = dict(zip(cat["request_type_code"], cat["agency"]))
    df["referred"] = df["outcome_class"] == "REFERRED"
    train, val, test = (df[df["split"] == s].copy() for s in common.SPLIT_ORDER)
    splits = {"val": val, "test": test}

    # =====================================================================
    # Part 1 - agency assignment candidates
    # =====================================================================
    # C1: majority agency per code, learned on train.
    c1 = train.groupby("request_type_code")["agency"].agg(lambda s: s.value_counts().idxmax()).to_dict()
    # C2: majority agency per (code, borough), backing off to C1.
    c2 = (train.groupby(["request_type_code", "borough"])["agency"]
               .agg(lambda s: s.value_counts().idxmax()).to_dict())

    # C3: final agency where it can be determined from the closure note.
    def final_agency(frame: pd.DataFrame) -> pd.Series:
        out = []
        for ag, ref, text in zip(frame["agency"], frame["referred"], frame["resolution_description"]):
            if not ref:
                out.append(ag)
                continue
            _, cls, dest = classify_destination(text, ag)
            out.append(dest if cls.startswith("EXPLICIT") or cls.startswith("INTERNAL") else None)
        return pd.Series(out, index=frame.index, dtype=object)

    for frame in (train, val, test):
        frame["final_agency"] = final_agency(frame)
    c3 = (train.dropna(subset=["final_agency"]).groupby("request_type_code")["final_agency"]
               .agg(lambda s: s.value_counts().idxmax()).to_dict())

    rows, changed = [], []
    for name, frame in splits.items():
        preds = {
            "A_catalog_lookup": frame["request_type_code"].map(cat_agency),
            "C1_majority_by_code": frame["request_type_code"].map(c1),
            "C2_majority_by_code_borough": pd.Series(
                [c2.get((c, b), c1.get(c)) for c, b in zip(frame["request_type_code"], frame["borough"])],
                index=frame.index),
            "C3_final_agency_majority": frame["request_type_code"].map(c3),
        }
        det = frame["final_agency"].notna()
        for approach, pred in preds.items():
            rows.append({
                "split": name, "approach": approach, "records": len(frame),
                "coverage": round(float(pred.notna().mean()), 6),
                "agree_initial_agency": round(float((pred == frame["agency"]).mean()), 6),
                "determinable_final_records": int(det.sum()),
                "agree_final_agency_determinable": round(float((pred[det] == frame.loc[det, "final_agency"]).mean()), 6),
                "differs_from_catalog": int((pred != frame["request_type_code"].map(cat_agency)).sum()),
            })
    for code in sorted(cat_agency):
        for label, table in (("C1", c1), ("C3", c3)):
            if table.get(code) and table[code] != cat_agency[code]:
                changed.append({"approach": label, "request_type_code": code,
                                "catalog_agency": cat_agency[code], "learned_agency": table[code]})
    for (code, borough), ag in sorted(c2.items()):
        if ag != cat_agency[code]:
            changed.append({"approach": "C2", "request_type_code": f"{code}/{borough}",
                            "catalog_agency": cat_agency[code], "learned_agency": ag})
    agency_cmp = pd.DataFrame(rows)
    common.write_table(agency_cmp, "method_comparison_agency.csv")
    common.write_table(pd.DataFrame(changed, columns=["approach", "request_type_code", "catalog_agency",
                                                      "learned_agency"]), "learned_vs_catalog_changes.csv")

    # Per-code view of C3 on the evaluation splits (where it would differ).
    c3_codes = [c["request_type_code"] for c in changed if c["approach"] == "C3"]
    c3_detail = []
    for name, frame in splits.items():
        for code in c3_codes:
            sub = frame[(frame["request_type_code"] == code) & frame["final_agency"].notna()]
            c3_detail.append({"split": name, "request_type_code": code, "determinable_records": len(sub),
                              "final_is_catalog_agency": int((sub["final_agency"] == cat_agency[code]).sum()),
                              "final_is_learned_agency": int((sub["final_agency"] == c3[code]).sum()),
                              "excluded_undeterminable": int(((frame["request_type_code"] == code)
                                                              & frame["final_agency"].isna()).sum())})
    common.write_table(pd.DataFrame(c3_detail), "c3_affected_codes.csv")

    # =====================================================================
    # Part 2 - referral-risk knowledge (data mining for an advisory)
    # =====================================================================
    # Two-stage, validation-only selection so each smoothing level is tuned
    # separately: stage 1 picks the RT-level pseudo-count m_rt; stage 2 keeps
    # m_rt fixed and picks the (RT, borough) cell pseudo-count m_cell.
    risk_rows = []
    y_val = val["referred"].to_numpy()
    prior = float(train["referred"].mean())
    for m in PSEUDO_COUNTS_RT:
        rt_rates, _ = fit_rt_rates(train, m)
        p = predict_rt(val, rt_rates, prior)
        risk_rows.append({"split": "val", "model": "RT", "m_rt": m, "m_cell": None,
                          "log_loss": round(log_loss(y_val, p), 5), "brier": round(brier(y_val, p), 5),
                          "auc": round(auc(y_val, p), 4)})
    best_rt = min((r for r in risk_rows if r["model"] == "RT"), key=lambda r: r["log_loss"])
    m_rt = best_rt["m_rt"]
    rt_rates, _ = fit_rt_rates(train, m_rt)
    for mc in PSEUDO_COUNTS_CELL:
        cell = fit_rt_borough(train, mc, rt_rates, prior)
        p = predict_rt_borough(val, cell, rt_rates, prior)
        risk_rows.append({"split": "val", "model": "RT+borough", "m_rt": m_rt, "m_cell": mc,
                          "log_loss": round(log_loss(y_val, p), 5), "brier": round(brier(y_val, p), 5),
                          "auc": round(auc(y_val, p), 4)})
    best_rb = min((r for r in risk_rows if r["model"] == "RT+borough"), key=lambda r: r["log_loss"])
    m_cell = best_rb["m_cell"]
    for name, frame in splits.items():  # constant baseline for context
        y = frame["referred"].to_numpy()
        p = np.full(len(frame), prior)
        risk_rows.append({"split": name, "model": "constant_prior", "m_rt": None, "m_cell": None,
                          "log_loss": round(log_loss(y, p), 5), "brier": round(brier(y, p), 5), "auc": 0.5})

    # Pre-declared simplicity rule: keep the RT-only estimator unless adding
    # the optional borough slot improves validation log-loss by more than 1%
    # relative AND does not reduce validation AUC.
    rel_gain = (best_rt["log_loss"] - best_rb["log_loss"]) / best_rt["log_loss"]
    use_borough = rel_gain > 0.01 and best_rb["auc"] >= best_rt["auc"]
    selected = best_rb if use_borough else best_rt
    cell = fit_rt_borough(train, m_cell, rt_rates, prior) if use_borough else {}

    def predict_selected(frame: pd.DataFrame) -> np.ndarray:
        if use_borough:
            return predict_rt_borough(frame, cell, rt_rates, prior)
        return predict_rt(frame, rt_rates, prior)

    # Advisory threshold chosen on validation by F1 (ties -> higher threshold,
    # i.e. fewer advisories). Codes with too little train support never flag.
    train_n = train.groupby("request_type_code").size().to_dict()

    def supported(frame: pd.DataFrame) -> np.ndarray:
        return frame["request_type_code"].map(lambda c: train_n.get(c, 0) >= MIN_SUPPORT).to_numpy()

    p_val = predict_selected(val)
    thr_rows = [{"split": "val", "threshold": t, **flag_metrics(y_val, (p_val >= t) & supported(val))}
                for t in THRESHOLDS]
    thr_df = pd.DataFrame(thr_rows)
    threshold = float(thr_df.sort_values(["f1", "threshold"], ascending=[False, False]).iloc[0]["threshold"])

    # Single final evaluation on test of both estimators (selected one marked)
    # and of every threshold, so the report can show the full trade-off.
    y_test = test["referred"].to_numpy()
    cell_eval = fit_rt_borough(train, m_cell, rt_rates, prior)
    for model, p in (("RT", predict_rt(test, rt_rates, prior)),
                     ("RT+borough", predict_rt_borough(test, cell_eval, rt_rates, prior))):
        risk_rows.append({"split": "test", "model": model, "m_rt": m_rt,
                          "m_cell": m_cell if model == "RT+borough" else None,
                          "log_loss": round(log_loss(y_test, p), 5), "brier": round(brier(y_test, p), 5),
                          "auc": round(auc(y_test, p), 4), "selected": model == selected["model"]})
    p_test = predict_selected(test)
    for t in THRESHOLDS:
        thr_rows.append({"split": "test", "threshold": t,
                         **flag_metrics(y_test, (p_test >= t) & supported(test))})
    thr_df = pd.DataFrame(thr_rows)
    common.write_table(pd.DataFrame(risk_rows), "referral_risk_model_selection.csv")
    common.write_table(thr_df, "referral_advisory_thresholds.csv")
    test_at_thr = thr_df[(thr_df.split == "test") & (thr_df.threshold == threshold)].iloc[0].to_dict()
    val_at_thr = thr_df[(thr_df.split == "val") & (thr_df.threshold == threshold)].iloc[0].to_dict()

    # =====================================================================
    # Part 3 - runtime knowledge artifact (train only, RT-keyed, no Parquet
    # needed at runtime).
    # =====================================================================
    train_ref = train[train["referred"]].copy()
    parsed = [classify_destination(t, a) for t, a in zip(train_ref["resolution_description"], train_ref["agency"])]
    train_ref["destination_agency"] = [p[2] if p[1].startswith("EXPLICIT") else "" for p in parsed]
    codes = {}
    g = train.groupby("request_type_code")["referred"].agg(["sum", "size"])
    for code in sorted(cat_agency):
        n = int(g.loc[code, "size"]) if code in g.index else 0
        k = int(g.loc[code, "sum"]) if code in g.index else 0
        rate = rt_rates.get(code, prior)
        if n < MIN_SUPPORT:
            level = "INSUFFICIENT_EVIDENCE"
        else:
            level = "ELEVATED" if rate >= threshold else "TYPICAL"
        dests = train_ref.loc[(train_ref["request_type_code"] == code) & (train_ref["destination_agency"] != ""),
                              "destination_agency"].value_counts()
        mm = mmap.loc[code]
        contextual = mm["relationship"] in ("MULTI_AGENCY_CONTEXTUAL", "CONSISTENT_CAUSE_DEPENDENT",
                                            "CONSISTENT_WITH_BLOCKED_STREET_OVERLAP")
        codes[code] = {
            "catalog_agency": cat_agency[code],
            "train_records": n,
            "train_referred": k,
            "referral_rate_raw": round(k / n, 4) if n else None,
            "referral_rate_smoothed": round(float(rate), 4),
            "risk_level": level,
            "matrix_relationship": mm["relationship"],
            "matrix_agencies": [a for a in mm["matrix_agencies"].split("|") if a],
            "context_dimension": mm["context_dimension"] or None,
            "context_in_co1_contract": False if contextual else None,
            "referral_rate_by_borough": ({b: round(float(r), 4) for (c, b), r in sorted(cell.items()) if c == code}
                                         if use_borough else {}),
            "observed_referral_destinations": {a: int(v) for a, v in dests.items()},
            "matrix_note": mm["note"],
        }
    # Same newline-normalised hash the runtime uses to detect stale knowledge.
    catalog_sha = catalog_content_hash(common.CATALOG_PATH)
    history_sha = hashlib.sha256(common.PARQUET_PATH.read_bytes()).hexdigest()
    knowledge = {
        "artifact": "civicops_agency.referral_knowledge",
        "schema_version": 1,
        "generated_by": "research/method_comparison.py",
        "generated_at_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "purpose": ("Informational referral-risk advisory per RT code. It never changes the catalog agency, "
                    "the required skills or dispatch eligibility unless a reviewer threshold is configured."),
        "fitted_on_split": "train",
        "train_window": [str(train["created_date"].min()), str(train["created_date"].max())],
        "train_records": int(len(train)),
        "global_train_referral_rate": round(prior, 4),
        "estimator": (f"m-estimate P(referred | RT, borough): cell m={m_cell} shrunk to P(referred | RT) m={m_rt}"
                      if use_borough else f"m-estimate P(referred | RT), m={m_rt}, prior=global train rate"),
        "uses_borough_slot": bool(use_borough),
        "selected_on": "val log-loss (model) and val F1 (threshold)",
        "elevated_threshold": threshold,
        "min_support": MIN_SUPPORT,
        "catalog_sha256": catalog_sha,
        "history_sha256": history_sha,
        "codes": codes,
    }
    common.KNOWLEDGE_OUT.write_text(json.dumps(knowledge, indent=2), encoding="utf-8")

    summary = {
        "agency_comparison": agency_cmp.to_dict("records"),
        "learned_vs_catalog_changes": changed,
        "c3_affected_codes": c3_detail,
        "risk_selected_model": selected["model"],
        "risk_selected_m_rt": m_rt,
        "risk_selected_m_cell": m_cell if use_borough else None,
        "risk_uses_borough": bool(use_borough),
        "risk_best_rt_val": best_rt,
        "risk_best_rtborough_val": best_rb,
        "risk_rt_vs_rtborough_val_logloss": [best_rt["log_loss"], best_rb["log_loss"]],
        "risk_rel_gain_borough": round(rel_gain, 5),
        "risk_val_best": selected,
        "risk_test": [r for r in risk_rows if r["split"] == "test"],
        "advisory_threshold": threshold,
        "advisory_val": val_at_thr,
        "advisory_test": test_at_thr,
        "elevated_codes": [c for c, v in codes.items() if v["risk_level"] == "ELEVATED"],
        "insufficient_codes": [c for c, v in codes.items() if v["risk_level"] == "INSUFFICIENT_EVIDENCE"],
        "knowledge_artifact": str(common.KNOWLEDGE_OUT.relative_to(common.ROOT)).replace("\\", "/"),
    }
    common.update_summary("method_comparison", summary)
    print(agency_cmp.to_string(index=False))
    print(pd.DataFrame(changed).to_string(index=False))
    print(pd.DataFrame(c3_detail).to_string(index=False))
    print(pd.DataFrame(risk_rows).to_string(index=False))
    print(thr_df.to_string(index=False))
    for k in ("risk_selected_model", "risk_selected_m_rt", "risk_selected_m_cell", "risk_rel_gain_borough",
              "advisory_threshold", "advisory_val", "advisory_test", "elevated_codes", "insufficient_codes"):
        print(k, summary[k])


if __name__ == "__main__":
    main()
