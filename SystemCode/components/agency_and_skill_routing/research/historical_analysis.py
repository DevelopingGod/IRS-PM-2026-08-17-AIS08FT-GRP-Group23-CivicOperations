"""Phase 2 research: historical knowledge discovery on RT-code/agency relationships.

Covers the mandatory analyses 1-9, 12 and 13 of the task brief (referrals,
items 10-11 and 14-15, live in ``referral_analysis.py``):

1.  Records per split and split boundaries (with an overlap check).
2.  Distinct RT codes and their frequencies (overall and per split).
3.  Catalog code coverage in the historical data.
4.  Historical agency distribution for every RT code.
5.  Catalog agency versus historical agency, per code.
6.  RT codes associated with more than one historical agency.
7.  Every catalog/historical disagreement (full record list).
8.  Complaint-type/descriptor patterns per code.
9.  Location-type patterns per code.
12. Temporal stability of the code -> agency relationship (by split and month).
13. Missing values and rare categories.

Additionally it reconstructs each historical RT code from ``complaint_type`` and
``descriptor`` using the catalog's own matching columns. If the reconstruction
is exact, the historical RT label is a deterministic function of the 311
complaint taxonomy, which matters for interpreting "agreement with history".

Only the ``train`` split is used for anything that later feeds the runtime
(see ``method_comparison.py``); this script is descriptive and reports all
splits separately so temporal drift is visible.

Run:  python research/historical_analysis.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402


def reconstruct_codes(df: pd.DataFrame, cat: pd.DataFrame) -> pd.Series:
    """Re-derive each record's RT code from complaint_type + descriptor.

    The catalog lists the 311 complaint types for each code ('|' separated) and
    an optional descriptor regular expression. A code with an empty regex acts
    as the fall-back for its complaint type. Returns the reconstructed code, or
    ``"AMBIGUOUS"``/``"NO_MATCH"`` when the rules do not single one out.
    """
    rules = []
    for _, row in cat.iterrows():
        ctypes = [c.strip() for c in row["complaint_type"].split("|") if c.strip()]
        rx = re.compile(row["descriptor_regex"]) if row["descriptor_regex"] else None
        rules.append((row["request_type_code"], set(ctypes), rx))

    cache: dict[tuple[str, str], str] = {}

    def one(ct: str, desc: str) -> str:
        key = (ct, desc)
        if key in cache:
            return cache[key]
        cands = [(code, rx) for code, cts, rx in rules if ct in cts]
        hits = [code for code, rx in cands if rx is not None and rx.search(desc or "")]
        if not hits:
            hits = [code for code, rx in cands if rx is None]
        result = hits[0] if len(hits) == 1 else ("NO_MATCH" if not hits else "AMBIGUOUS")
        cache[key] = result
        return result

    return pd.Series([one(c, d) for c, d in zip(df["complaint_type"], df["descriptor"].fillna(""))],
                     index=df.index)


def main() -> None:
    df = common.load_history()
    cat = common.load_catalog()
    cat_agency = dict(zip(cat["request_type_code"], cat["agency"]))
    df["catalog_agency_csv"] = df["request_type_code"].map(cat_agency)
    df["agrees_with_catalog"] = df["agency"] == df["catalog_agency_csv"]
    df["referred"] = df["outcome_class"] == "REFERRED"
    df["month"] = df["created_date"].dt.to_period("M").astype(str)

    # ---- 1. Split inventory and boundaries --------------------------------
    splits = (df.groupby("split")
                .agg(records=("unique_key", "size"), first_created=("created_date", "min"),
                     last_created=("created_date", "max"), referred=("referred", "sum"))
                .reindex(common.SPLIT_ORDER).reset_index())
    common.write_table(splits, "split_inventory.csv")
    # Strict temporal ordering: the last train record must precede the first
    # validation record, and validation must precede test.
    ordered = (splits.loc[0, "last_created"] < splits.loc[1, "first_created"]
               and splits.loc[1, "last_created"] < splits.loc[2, "first_created"])

    # ---- 2. RT frequencies --------------------------------------------------
    freq = (pd.crosstab(df["request_type_code"], df["split"]).reindex(columns=common.SPLIT_ORDER, fill_value=0))
    freq["total"] = freq.sum(axis=1)
    freq["share_pct"] = (100 * freq["total"] / len(df)).round(3)
    freq = freq.sort_values("total", ascending=False).reset_index()
    common.write_table(freq, "rt_frequency.csv")

    # ---- 3. Coverage --------------------------------------------------------
    hist_codes, cat_codes = set(df["request_type_code"]), set(cat["request_type_code"])

    # ---- 4. Agency distribution per code -----------------------------------
    dist = pd.crosstab(df["request_type_code"], df["agency"])
    common.write_table(dist.reset_index(), "rt_agency_distribution.csv")

    # ---- 5/6. Catalog vs historical agency per code -------------------------
    per_code = df.groupby("request_type_code").agg(
        records=("unique_key", "size"), catalog_agency=("catalog_agency_csv", "first"),
        agree=("agrees_with_catalog", "sum"), distinct_hist_agencies=("agency", "nunique"),
        majority_hist_agency=("agency", lambda s: s.value_counts().idxmax()))
    per_code["disagree"] = per_code["records"] - per_code["agree"]
    per_code["agreement_rate"] = (per_code["agree"] / per_code["records"]).round(6)
    per_code["hist_agencies"] = df.groupby("request_type_code")["agency"].apply(
        lambda s: "; ".join(f"{a}={n}" for a, n in s.value_counts().items()))
    per_code = per_code.reset_index()
    common.write_table(per_code, "catalog_vs_historical_by_code.csv")
    multi = per_code[per_code["distinct_hist_agencies"] > 1]
    common.write_table(multi, "multi_agency_rt_codes.csv")

    # ---- 7. Every disagreement (full list, no sampling) ---------------------
    dis_cols = ["unique_key", "split", "created_date", "request_type_code", "catalog_agency_csv", "agency",
                "agency_name", "complaint_type", "descriptor", "location_type", "borough",
                "status", "outcome_class", "resolution_description"]
    disagreements = df.loc[~df["agrees_with_catalog"], dis_cols]
    common.write_table(disagreements, "catalog_historical_disagreements.csv")

    # ---- 8/9. Descriptor and location-type patterns per code ----------------
    desc = (df.groupby(["request_type_code", "complaint_type", "descriptor"], dropna=False)
              .agg(records=("unique_key", "size"), referred=("referred", "sum"),
                   distinct_agencies=("agency", "nunique"))
              .reset_index())
    desc["referral_rate"] = (desc["referred"] / desc["records"]).round(4)
    common.write_table(desc, "descriptor_patterns_by_code.csv")
    loc = (df.assign(location_type=df["location_type"].fillna("<missing>"))
             .groupby(["request_type_code", "location_type"])
             .agg(records=("unique_key", "size"), referred=("referred", "sum"),
                  distinct_agencies=("agency", "nunique"))
             .reset_index())
    loc["referral_rate"] = (loc["referred"] / loc["records"]).round(4)
    common.write_table(loc, "location_type_patterns_by_code.csv")

    # ---- 12. Temporal stability -------------------------------------------
    by_split = (df.groupby(["request_type_code", "split"])
                  .agg(records=("unique_key", "size"), agree=("agrees_with_catalog", "sum"),
                       referred=("referred", "sum"))
                  .reset_index())
    by_split["agreement_rate"] = (by_split["agree"] / by_split["records"]).round(6)
    by_split["referral_rate"] = (by_split["referred"] / by_split["records"]).round(4)
    common.write_table(by_split, "temporal_stability_by_split.csv")
    by_month = (df.groupby(["month"])
                  .agg(records=("unique_key", "size"), agree=("agrees_with_catalog", "sum"),
                       referred=("referred", "sum"), distinct_codes=("request_type_code", "nunique"))
                  .reset_index())
    by_month["agreement_rate"] = (by_month["agree"] / by_month["records"]).round(6)
    by_month["referral_rate"] = (by_month["referred"] / by_month["records"]).round(4)
    common.write_table(by_month, "temporal_stability_by_month.csv")
    codes_all_splits = int((freq[common.SPLIT_ORDER] > 0).all(axis=1).sum())

    # ---- 13. Missing values and rare categories ----------------------------
    miss = df.isna().groupby(df["split"]).sum().T.reindex(columns=common.SPLIT_ORDER)
    miss["total_missing"] = miss.sum(axis=1)
    miss["missing_pct"] = (100 * miss["total_missing"] / len(df)).round(3)
    miss = miss[miss["total_missing"] > 0].reset_index().rename(columns={"index": "column"})
    common.write_table(miss, "missing_values_by_split.csv")
    rare_codes = freq[freq["total"] < 0.01 * len(df)][["request_type_code", "total", "share_pct"]]
    rare_in_eval = freq[(freq["val"] < 30) | (freq["test"] < 30)][["request_type_code", "val", "test"]]
    rare_desc = desc[desc["records"] < 5]
    common.write_table(rare_desc, "rare_descriptors.csv")

    # ---- Label provenance: can the RT code be rebuilt from 311 fields? -----
    df["reconstructed_code"] = reconstruct_codes(df, cat)
    recon_ok = df["reconstructed_code"] == df["request_type_code"]
    recon = (df.loc[~recon_ok, ["unique_key", "request_type_code", "reconstructed_code",
                                 "complaint_type", "descriptor"]])
    common.write_table(recon, "rt_reconstruction_mismatches.csv")
    # Does each complaint type map to a single agency in raw 311 data?
    ct_agency = df.groupby("complaint_type")["agency"].nunique()

    summary = {
        "records": int(len(df)),
        "columns": int(df.shape[1] - 6),  # exclude helper columns added above
        "split_counts": {s: int(n) for s, n in zip(splits["split"], splits["records"])},
        "split_boundaries": {r.split: [str(r.first_created), str(r.last_created)] for r in splits.itertuples()},
        "splits_strictly_ordered": bool(ordered),
        "source_values": df["source"].value_counts().to_dict(),
        "synthetic_rows": int(df["is_synthetic"].sum()),
        "duplicate_unique_keys": int(df["unique_key"].duplicated().sum()),
        "distinct_rt_codes": len(hist_codes),
        "catalog_codes_in_history": len(hist_codes & cat_codes),
        "catalog_codes_missing_from_history": sorted(cat_codes - hist_codes),
        "history_codes_not_in_catalog": sorted(hist_codes - cat_codes),
        "historical_agencies": df["agency"].value_counts().to_dict(),
        "overall_agreement": int(df["agrees_with_catalog"].sum()),
        "overall_agreement_rate": round(float(df["agrees_with_catalog"].mean()), 6),
        "disagreement_count": int((~df["agrees_with_catalog"]).sum()),
        "disagreements": disagreements[["unique_key", "split", "request_type_code", "catalog_agency_csv",
                                        "agency", "descriptor", "outcome_class"]].to_dict("records"),
        "multi_agency_codes": multi["request_type_code"].tolist(),
        "embedded_catalog_agency_matches_csv": bool((df["catalog_agency"] == df["catalog_agency_csv"]).all()),
        "rt_reconstruction_exact": int(recon_ok.sum()),
        "rt_reconstruction_mismatch": int((~recon_ok).sum()),
        "complaint_types": int(df["complaint_type"].nunique()),
        "complaint_types_with_multiple_agencies": ct_agency[ct_agency > 1].to_dict(),
        "agreement_by_split": df.groupby("split")["agrees_with_catalog"].mean().round(6).to_dict(),
        "codes_present_in_all_splits": codes_all_splits,
        "rare_codes_under_1pct": rare_codes.to_dict("records"),
        "codes_under_30_in_val_or_test": rare_in_eval.to_dict("records"),
        "rare_descriptor_rows_under_5": int(len(rare_desc)),
        "borough_values": df["borough"].value_counts(dropna=False).to_dict(),
        "location_type_missing": int(df["location_type"].isna().sum()),
        "status_values": df["status"].value_counts().to_dict(),
        "outcome_class_values": df["outcome_class"].value_counts().to_dict(),
    }
    common.update_summary("historical", summary)
    for k, v in summary.items():
        if k not in ("disagreements",):
            print(f"{k}: {v}")


if __name__ == "__main__":
    main()
