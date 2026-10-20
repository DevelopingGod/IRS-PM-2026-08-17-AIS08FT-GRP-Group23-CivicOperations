"""Phase 2 research: the REFERRED subset (mandatory analyses 10, 11, 14 and 15).

Why referrals matter: a REFERRED request is one whose first agency handed it
on. If those hand-offs follow patterns that are predictable from information
available at intake time, the router could do better than a plain catalog
lookup. If they depend on facts that only emerge on site, they cannot be fixed
at routing time and should be treated as retrospective error indicators.

The script answers, from the data rather than by assumption:

* Does ``agency`` record the initial agency or the final one?
* Is the destination agency recorded in a structured column? (No such column
  exists.) Can it be recovered from ``resolution_description`` without
  speculation, and for how many records?
* Which codes, agencies, descriptors, location types and boroughs carry the
  referrals, and are referral rates stable over time?
* Which referral patterns does the agency matrix explain, and which remain
  unresolved?

``resolution_description`` and ``outcome_class`` are after-the-fact fields.
They are read here for *retrospective* understanding only; nothing derived from
them is used as a runtime input. The destination parsing below is a
documentation aid, not a training target.

Run:  python research/referral_analysis.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

# Agency names as written in NYC 311 closure notes -> abbreviation.
AGENCY_PATTERNS = [
    (r"Department of Environmental Protection", "DEP"),
    (r"Department of Transportation", "DOT"),
    (r"Department of Sanitation|DSNY", "DSNY"),
    (r"Department of Parks and Recreation|NYC Parks", "DPR"),
    (r"Department of Buildings", "DOB"),
    (r"Department of Health and Mental Hygiene", "DOHMH"),
    (r"New York City Housing Authority", "NYCHA"),
    (r"Metropolitan Transportation Authority", "MTA"),
    (r"NYPD|New York City Police Department|New York Police Department|Police Department", "NYPD"),
]
CATALOG_AGENCIES = {"DEP", "DOB", "DOHMH", "DOT", "DPR", "DSNY", "HPD", "NYPD"}
REFERRAL_VERB = re.compile(r"referred|requested|forwarded", re.IGNORECASE)
INTERNAL_UNIT = re.compile(r"Inspections Unit|Arterial Division|Bridge Division", re.IGNORECASE)
UNSPECIFIED = re.compile(r"appropriate agency|another agency|which City agency", re.IGNORECASE)


def find_agencies(text: str) -> list[str]:
    """Return agency abbreviations in the order they are mentioned."""
    hits = []
    for pattern, abbr in AGENCY_PATTERNS:
        for m in re.finditer(pattern, text):
            hits.append((m.start(), abbr))
    # Drop overlapping duplicates (e.g. 'New York City Police Department' also
    # matches 'Police Department') by keeping the first abbreviation per position.
    seen, ordered = set(), []
    for _, abbr in sorted(hits):
        if not ordered or ordered[-1] != abbr:
            ordered.append(abbr)
        seen.add(abbr)
    return ordered


def classify_destination(text: str, origin: str) -> tuple[str, str, str]:
    """Classify a closure note into (author_agency, destination_class, destination_agency).

    Only explicit wording is used. When the note does not name a different
    agency after a referral verb, the destination is reported as unknown
    rather than guessed.
    """
    if not isinstance(text, str) or not text.strip():
        return "", "NO_TEXT", ""
    mentions = find_agencies(text)
    author = mentions[0] if mentions else ""
    if INTERNAL_UNIT.search(text):
        return author, "INTERNAL_UNIT_SAME_AGENCY", origin
    verb = REFERRAL_VERB.search(text)
    if verb:
        after = find_agencies(text[verb.start():])
        after = [a for a in after if a != origin]
        if after:
            dest = after[0]
            cls = "EXPLICIT_CATALOG_AGENCY" if dest in CATALOG_AGENCIES else "EXPLICIT_NON_CATALOG_AGENCY"
            return author, cls, dest
    if UNSPECIFIED.search(text):
        return author, "UNSPECIFIED_OTHER_AGENCY", ""
    return author, "NO_DESTINATION_STATED", ""


def main() -> None:
    df = common.load_history()
    cat = common.load_catalog()
    mmap = common.load_matrix_map().set_index("request_type_code")
    cat_agency = dict(zip(cat["request_type_code"], cat["agency"]))
    df["referred"] = df["outcome_class"] == "REFERRED"
    ref = df[df["referred"]].copy()

    # ---- Semantics of `agency` ---------------------------------------------
    parsed = ref.apply(lambda r: classify_destination(r["resolution_description"], r["agency"]), axis=1)
    ref["text_author_agency"] = [p[0] for p in parsed]
    ref["destination_class"] = [p[1] for p in parsed]
    ref["destination_agency"] = [p[2] for p in parsed]
    authored = ref[ref["text_author_agency"] != ""]
    author_matches_agency = float((authored["text_author_agency"] == authored["agency"]).mean())
    ref_agency_is_catalog = float((ref["agency"] == ref["request_type_code"].map(cat_agency)).mean())

    # Full list of distinct closure-note templates with their classification
    # (the notes are templated, so this is the complete evidence, not a sample).
    templates = (ref.groupby(["agency", "resolution_description", "text_author_agency",
                              "destination_class", "destination_agency"], dropna=False)
                   .size().rename("records").reset_index().sort_values("records", ascending=False))
    common.write_table(templates, "referral_text_templates.csv")

    # ---- 10. Referrals by (initial) agency ----------------------------------
    by_agency = (df.groupby("agency").agg(records=("referred", "size"), referred=("referred", "sum")).reset_index())
    by_agency["referral_rate"] = (by_agency["referred"] / by_agency["records"]).round(4)
    by_agency["share_of_referrals"] = (by_agency["referred"] / by_agency["referred"].sum()).round(4)
    common.write_table(by_agency.sort_values("referred", ascending=False), "referral_by_agency.csv")

    # ---- 11. Referral frequency by RT code (overall and per split) ---------
    by_code = df.groupby("request_type_code").agg(records=("referred", "size"), referred=("referred", "sum"))
    by_code["referral_rate"] = (by_code["referred"] / by_code["records"]).round(4)
    rates = (df.groupby(["request_type_code", "split"])["referred"].mean().unstack()
               .reindex(columns=common.SPLIT_ORDER).round(4).add_prefix("rate_"))
    counts = (df.groupby(["request_type_code", "split"])["referred"].sum().unstack()
                .reindex(columns=common.SPLIT_ORDER).fillna(0).astype(int).add_prefix("referred_"))
    by_code = by_code.join(counts).join(rates)
    by_code["catalog_agency"] = by_code.index.map(cat_agency)
    by_code["matrix_relationship"] = by_code.index.map(mmap["relationship"])
    by_code = by_code.sort_values("referred", ascending=False).reset_index()
    common.write_table(by_code, "referral_by_code.csv")
    # Spearman-free stability check: correlation of per-code rates between splits.
    stab = by_code[["rate_train", "rate_val", "rate_test"]].dropna()
    stability = {"pearson_train_val": round(float(stab["rate_train"].corr(stab["rate_val"])), 4),
                 "pearson_train_test": round(float(stab["rate_train"].corr(stab["rate_test"])), 4),
                 "spearman_train_test": round(float(stab["rate_train"].rank().corr(stab["rate_test"].rank())), 4),
                 "codes_compared": int(len(stab))}

    # ---- Destinations recovered from text (retrospective only) -------------
    dest = (ref.groupby(["request_type_code", "agency", "destination_class", "destination_agency"])
              .size().rename("records").reset_index().sort_values(["request_type_code", "records"],
                                                                   ascending=[True, False]))
    common.write_table(dest, "referral_destinations_by_code.csv")
    dest_class = ref["destination_class"].value_counts().to_dict()
    dest_agency = ref.loc[ref["destination_agency"] != "", "destination_agency"].value_counts().to_dict()

    # ---- 14/15. Matrix explanation of each code's referrals -----------------
    rows = []
    for code, grp in ref.groupby("request_type_code"):
        m = mmap.loc[code]
        alternatives = set(m["matrix_agencies"].split("|")) - {cat_agency[code]}
        explicit = grp[grp["destination_class"].str.startswith("EXPLICIT")]
        internal = int((grp["destination_class"] == "INTERNAL_UNIT_SAME_AGENCY").sum())
        to_alt = int(explicit["destination_agency"].isin(alternatives).sum())
        to_other_catalog = int((explicit["destination_agency"].isin(CATALOG_AGENCIES)
                                & ~explicit["destination_agency"].isin(alternatives)).sum())
        to_non_catalog = int((grp["destination_class"] == "EXPLICIT_NON_CATALOG_AGENCY").sum())
        unknown = len(grp) - len(explicit) - internal
        if to_alt and to_alt >= 0.5 * len(explicit):
            verdict = "EXPLAINED_BY_MATRIX_MULTI_AGENCY_FAMILY"
        elif m["relationship"] == "CONSISTENT_CAUSE_DEPENDENT" and to_other_catalog:
            verdict = "EXPLAINED_BY_MATRIX_CAUSE_DEPENDENT"
        elif internal == len(grp):
            verdict = "NOT_AN_AGENCY_CHANGE"
        elif len(explicit) == 0:
            verdict = "UNRESOLVED_NO_DESTINATION_RECORDED"
        else:
            verdict = "UNRESOLVED_PARTIAL_EVIDENCE"
        rows.append({
            "request_type_code": code, "catalog_agency": cat_agency[code], "referred": len(grp),
            "matrix_relationship": m["relationship"], "context_dimension": m["context_dimension"],
            "context_in_co1_contract": m["context_in_co1_contract"] or "N/A",
            "explicit_destination": len(explicit), "to_matrix_alternative": to_alt,
            "to_other_catalog_agency": to_other_catalog, "to_non_catalog_agency": to_non_catalog,
            "internal_unit": internal, "destination_unknown": unknown,
            "top_destination": (explicit["destination_agency"].value_counts().idxmax() if len(explicit) else ""),
            "verdict": verdict,
        })
    explain = pd.DataFrame(rows).sort_values("referred", ascending=False)
    common.write_table(explain, "referral_matrix_explanation.csv")

    # ---- Context patterns inside high-referral codes (offline features) -----
    hot = by_code.loc[by_code["referred"] >= 20, "request_type_code"].tolist()
    ctx_rows = []
    for col in ["descriptor", "location_type", "borough", "channel"]:
        sub = (df[df["request_type_code"].isin(hot)]
               .assign(value=lambda d: d[col].fillna("<missing>"))
               .groupby(["request_type_code", "value"])["referred"].agg(["size", "sum"]).reset_index())
        sub["feature"] = col
        sub["available_at_runtime"] = col == "borough"  # only borough is a CO1 slot
        ctx_rows.append(sub)
    ctx = pd.concat(ctx_rows).rename(columns={"size": "records", "sum": "referred"})
    ctx["referral_rate"] = (ctx["referred"] / ctx["records"]).round(4)
    common.write_table(ctx[["request_type_code", "feature", "available_at_runtime", "value", "records",
                            "referred", "referral_rate"]], "referral_context_patterns.csv")

    # How much the referral rate varies *within* a code by feature: the spread
    # (max-min rate across values with >= 20 records) shows where context
    # matters. A large spread on an unavailable feature is unusable at runtime.
    spread = (ctx[ctx["records"] >= 20].groupby(["request_type_code", "feature"])["referral_rate"]
                .agg(lambda s: round(float(s.max() - s.min()), 4)).unstack())
    common.write_table(spread.reset_index(), "referral_context_spread.csv")

    summary = {
        "referred_records": int(len(ref)),
        "referred_by_split": ref["split"].value_counts().reindex(common.SPLIT_ORDER).to_dict(),
        "referral_rate_overall": round(float(df["referred"].mean()), 4),
        "verified_outcome_of_referred": ref["verified_outcome"].value_counts().to_dict(),
        "status_of_referred": ref["status"].value_counts().to_dict(),
        "structured_destination_column_exists": False,
        "text_author_identified": int(len(authored)),
        "text_author_equals_agency_rate": round(author_matches_agency, 4),
        "referred_agency_equals_catalog_rate": round(ref_agency_is_catalog, 4),
        "destination_classes": dest_class,
        "destination_agencies": dest_agency,
        "explicit_destination_records": int(ref["destination_class"].str.startswith("EXPLICIT").sum()),
        "codes_with_referrals": int(ref["request_type_code"].nunique()),
        "top_codes": by_code.head(8)[["request_type_code", "records", "referred", "referral_rate"]].to_dict("records"),
        "rate_stability": stability,
        "matrix_verdicts": explain["verdict"].value_counts().to_dict(),
        "matrix_explained_codes": explain.loc[explain.verdict.str.startswith("EXPLAINED"), "request_type_code"].tolist(),
        "unresolved_codes": explain.loc[explain.verdict.str.startswith("UNRESOLVED"), "request_type_code"].tolist(),
        "high_referral_codes": hot,
    }
    common.update_summary("referrals", summary)
    for k, v in summary.items():
        print(f"{k}: {v}")
    print(explain.to_string(index=False))


if __name__ == "__main__":
    main()
