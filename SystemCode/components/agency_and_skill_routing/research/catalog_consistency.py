"""Phase 1/2 research: catalog integrity and catalog-versus-matrix consistency.

Questions answered (task section 5.1 and 5.2):

* Are RT codes unique, well formed and complete (agency + skill present)?
* Does every valid code determine exactly one agency and one skill set?
* Which agencies and skills does the catalog use, and do they match the
  allowed values agreed in the shared data reference?
* For every code, which agency-matrix family does it belong to, and does the
  matrix name the catalog agency? Where the matrix lists several agencies,
  which contextual dimension decides between them, and does the CO1 contract
  carry that context?

The matrix is *not* used to override the catalog. Disagreements are recorded
as evidence for the decision register.

Run:  python research/catalog_consistency.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import common  # noqa: E402

# Allowed values transcribed from the shared data reference (page 3), used only
# to cross-check the catalog against the team agreement.
SHARED_AGENCIES = {"DEP", "DOB", "DOHMH", "DOT", "DPR", "DSNY", "HPD", "NYPD"}
SHARED_SKILLS = {
    "BUILDING_INSPECTION", "FORESTRY", "HOUSING_INSPECTION", "NOISE_PATROL",
    "PARKING_ENFORCEMENT", "PEST_CONTROL", "ROAD_REPAIR", "SANITATION_COLLECTION",
    "SEWER_DRAINAGE", "SIDEWALK_REPAIR", "SNOW_ICE", "STREET_CLEANING",
    "STREET_LIGHTING", "TRAFFIC_SIGNALS", "VEHICLE_REMOVAL", "WATER_MAINS",
}
CODE_PATTERN = re.compile(r"^RT\d{3}$")


def parse_matrix(path: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Parse the two markdown tables in the agency matrix.

    Returns ``(families, glossary)``. Agency abbreviations in the family table
    are written in bold (``**DEP**``), which is what we extract.
    """
    families, glossary = [], []
    section = "families"
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("## Acronym glossary"):
            section = "glossary"
        if not line.startswith("|") or set(line.replace("|", "").strip()) <= {"-"}:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells[0] in ("Complaint or request family", "Acronym"):
            continue  # header rows
        if section == "families":
            agencies = re.findall(r"\*\*([A-Z0-9/]+)\*\*", cells[1])
            families.append({"matrix_family": cells[0], "agencies": "|".join(agencies),
                             "n_agencies": len([a for a in agencies if a != "911"])})
        else:
            glossary.append({"acronym": cells[0], "full_name": cells[1]})
    return pd.DataFrame(families), pd.DataFrame(glossary)


def main() -> None:
    cat = common.load_catalog()
    fam, glossary = parse_matrix(common.MATRIX_PATH)
    mmap = common.load_matrix_map()

    # ---------------- Catalog integrity ----------------
    codes = cat["request_type_code"].str.strip()
    dup_codes = sorted(codes[codes.duplicated(keep=False)].unique())
    bad_format = sorted(c for c in codes if not CODE_PATTERN.match(c))
    missing_agency = sorted(cat.loc[cat["agency"].str.strip() == "", "request_type_code"])
    missing_skill = sorted(cat.loc[cat["required_skill"].str.strip() == "", "request_type_code"])
    multi_skill = sorted(cat.loc[cat["required_skill"].str.contains(r"\|"), "request_type_code"])
    # A code is "conflicting" if it appears more than once with different
    # agency/skill values; identical duplicates would merely be redundant.
    per_code = cat.groupby("request_type_code").agg(
        n_rows=("agency", "size"), n_agencies=("agency", "nunique"), n_skills=("required_skill", "nunique"))
    conflicting = sorted(per_code[(per_code.n_agencies > 1) | (per_code.n_skills > 1)].index)
    agencies = set(cat["agency"])
    skills = set(s for v in cat["required_skill"] for s in v.split("|") if s)

    inventory = cat[["request_type_code", "citizen_label", "complaint_family", "agency",
                     "required_skill", "dispatchable"]].copy()
    inventory["code_well_formed"] = inventory["request_type_code"].map(lambda c: bool(CODE_PATTERN.match(c)))
    inventory["n_catalog_rows"] = inventory["request_type_code"].map(per_code["n_rows"])
    inventory["uniquely_determined"] = inventory["request_type_code"].map(
        lambda c: per_code.loc[c, "n_rows"] == 1 and per_code.loc[c, "n_agencies"] == 1)
    common.write_table(inventory, "catalog_inventory.csv")

    # ---------------- Catalog versus matrix ----------------
    cvm = cat[["request_type_code", "citizen_label", "agency"]].merge(mmap, on="request_type_code", how="left")
    cvm["catalog_agency_named_in_matrix"] = [
        a in str(m).split("|") for a, m in zip(cvm["agency"], cvm["matrix_agencies"])]
    common.write_table(cvm, "catalog_vs_matrix.csv")
    common.write_table(fam, "matrix_families.csv")

    matrix_agencies = set(a for v in fam["agencies"] for a in v.split("|") if a and a != "911")
    summary = {
        "catalog_rows": int(len(cat)),
        "catalog_columns": list(cat.columns),
        "distinct_codes": int(codes.nunique()),
        "duplicate_codes": dup_codes,
        "conflicting_codes": conflicting,
        "malformed_codes": bad_format,
        "missing_agency_codes": missing_agency,
        "missing_skill_codes": missing_skill,
        "multi_skill_codes": multi_skill,
        "every_code_uniquely_determined": bool(inventory["uniquely_determined"].all()),
        "catalog_agencies": sorted(agencies),
        "catalog_skills": sorted(skills),
        "agencies_match_shared_reference": agencies == SHARED_AGENCIES,
        "skills_match_shared_reference": skills == SHARED_SKILLS,
        "dispatchable_values": sorted(cat["dispatchable"].unique()),
        "matrix_family_rows": int(len(fam)),
        "matrix_glossary_rows": int(len(glossary)),
        "matrix_multi_agency_families": fam.loc[fam.n_agencies > 1, "matrix_family"].tolist(),
        "matrix_agencies_not_in_catalog": sorted(matrix_agencies - agencies),
        "catalog_agencies_missing_from_glossary": sorted(agencies - set(glossary["acronym"])),
        "relationship_counts": cvm["relationship"].value_counts().to_dict(),
        "catalog_agency_not_named_in_matrix": cvm.loc[~cvm.catalog_agency_named_in_matrix,
                                                       "request_type_code"].tolist(),
        "multi_agency_contextual_codes": cvm.loc[cvm.relationship == "MULTI_AGENCY_CONTEXTUAL",
                                                  "request_type_code"].tolist(),
        "context_available_in_co1": sorted(set(cvm["context_in_co1_contract"]) - {""}),
    }
    common.update_summary("catalog", summary)
    for k, v in summary.items():
        print(f"{k}: {v}")


if __name__ == "__main__":
    main()
