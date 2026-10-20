"""Phase 4a - Historical data validation (schema, splits, coordinate availability, precision).

This step touches all three splits for data-quality counts only. It computes no proximity
statistics on the test split. Outputs go to outputs/tables/ and outputs/charts/.

Usage: python research/explore_requests.py
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from common import CATALOG, CHARTS, DATA, ROOT, TABLES, load_requests

import sys
sys.path.insert(0, str(ROOT))
from civicops_external_factors.schemas import LEAKAGE_FIELDS  # noqa: E402
from civicops_external_factors.spatial_engine import SpatialEngine  # noqa: E402
from civicops_external_factors.osm_loader import load_facility_cache  # noqa: E402
from visualization import style  # noqa: E402


def main() -> None:
    TABLES.mkdir(parents=True, exist_ok=True)
    raw = pd.read_parquet(DATA)
    schema = pd.DataFrame({"column": raw.columns, "dtype": [str(t) for t in raw.dtypes],
                           "non_null": raw.notna().sum().values,
                           "used_by_component": [c in ("request_type_code", "latitude", "longitude", "geo_valid",
                                                       "address_type", "borough", "location_type", "split",
                                                       "request_id", "created_at", "complaint_family")
                                                 for c in raw.columns],
                           "leakage_excluded": [c in LEAKAGE_FIELDS for c in raw.columns]})
    schema.to_csv(TABLES / "data_schema.csv", index=False)

    df = load_requests()
    cat = pd.read_csv(CATALOG)
    summary = {
        "rows": int(len(raw)), "columns": int(raw.shape[1]),
        "request_id_unique": bool(raw["request_id"].is_unique),
        "source_values": raw["source"].value_counts().to_dict(),
        "is_synthetic_true": int(raw["is_synthetic"].sum()),
        "leakage_fields_dropped": [c for c in raw.columns if c in LEAKAGE_FIELDS],
        "weather_fields_ignored": [c for c in raw.columns if c.startswith("wx_")],
        "catalog_request_types": int(len(cat)),
        "request_types_in_data": int(df["request_type_code"].nunique()),
        "request_types_not_in_catalog": sorted(set(df["request_type_code"].dropna()) - set(cat["request_type_code"])),
        "catalog_types_absent_from_data": sorted(set(cat["request_type_code"]) - set(df["request_type_code"].dropna())),
    }
    split = (df.groupby("split").agg(rows=("request_id", "size"), first=("created_at", "min"), last=("created_at", "max"),
                                     geo_valid=("geo_valid", "sum"))
             .reindex(["train", "val", "test"]))
    split["geo_valid_pct"] = 100 * split["geo_valid"] / split["rows"]
    split.to_csv(TABLES / "split_summary.csv")
    order = ["train", "val", "test"]
    bounds_ok = (split.loc["train", "last"] < split.loc["val", "first"]) and (split.loc["val", "last"] < split.loc["test", "first"])
    summary["split_boundaries_chronological_non_overlapping"] = bool(bounds_ok)
    summary["split_ranges"] = {s: [str(split.loc[s, "first"]), str(split.loc[s, "last"])] for s in order}

    geo = pd.crosstab([df["split"], df["borough"]], df["geo_valid"]).rename(columns={True: "valid", False: "missing"})
    geo["missing_pct"] = 100 * geo["missing"] / (geo["valid"] + geo["missing"])
    geo.to_csv(TABLES / "geo_validity_by_split_borough.csv")
    prec = pd.crosstab(df["location_precision"], df["split"])[order]
    prec["all_pct"] = 100 * prec.sum(axis=1) / prec.values.sum()
    prec.to_csv(TABLES / "location_precision_by_split.csv")
    pg = pd.crosstab(df["address_type"].fillna("<NA>"), df["geo_valid"])
    pg.to_csv(TABLES / "address_type_vs_geo_valid.csv")
    rt = pd.crosstab(df["request_type_code"], df["split"])[order]
    rt["all"] = rt.sum(axis=1)
    rt = rt.join(cat.set_index("request_type_code")[["citizen_label", "complaint_family"]]).sort_values("all", ascending=False)
    rt.to_csv(TABLES / "request_type_by_split.csv")
    rtg = df.groupby("request_type_code")["geo_valid"].agg(["size", "mean"]).rename(columns={"mean": "geo_valid_rate"})
    rtg.sort_values("geo_valid_rate").to_csv(TABLES / "geo_validity_by_request_type.csv")

    # Coordinate sanity: inside the five-borough union, and borough label vs coordinate borough.
    cache = load_facility_cache(ROOT / "data" / "osm")
    eng = SpatialEngine(cache.facilities, cache.boroughs, "EPSG:32118")
    v = df[df["geo_valid"]].copy()
    pts = eng.project(v["latitude"].to_numpy(), v["longitude"].to_numpy())
    v["inside_nyc"] = eng.in_coverage(pts, 0.0)
    v["borough_from_coords"] = eng.borough_of(pts)
    mismatch = v[v["borough_from_coords"].notna() & (v["borough_from_coords"] != v["borough"])]
    summary["valid_coordinates"] = int(len(v))
    summary["valid_coordinates_outside_nyc_union"] = int((~v["inside_nyc"]).sum())
    summary["borough_label_vs_coordinate_mismatch"] = int(len(mismatch))
    summary["borough_mismatch_pairs"] = (mismatch.groupby(["borough", "borough_from_coords"]).size()
                                         .sort_values(ascending=False).head(10).rename("n").reset_index()
                                         .astype(str).to_dict("records"))
    summary["duplicate_coordinates_share"] = round(float(v.duplicated(["latitude", "longitude"]).mean()), 4)
    summary["sampling_note"] = ("Rows come in borough-stratified sample windows (sample_window_id); each borough "
                                "has ~10k rows, so per-borough rates are comparable but citywide rates are NOT "
                                "volume-weighted to real 311 traffic.")
    (TABLES / "data_validation_summary.json").write_text(json.dumps(summary, indent=2, default=str), "utf-8")

    # Chart: missing-coordinate rate by borough and split (small grouped bars).
    style.apply()
    import matplotlib.pyplot as plt
    g = geo.reset_index()
    fig, ax = plt.subplots(figsize=(7.5, 3.6))
    boroughs = style.BOROUGH_ORDER
    w = 0.26
    x = np.arange(len(boroughs))
    for k, s in enumerate(order):
        vals = [g[(g.split == s) & (g.borough == b)]["missing_pct"].iloc[0] for b in boroughs]
        ax.bar(x + (k - 1) * w, vals, w - 0.03, color=style.SPLIT_COLOR[s], label=s)
    ax.set_xticks(x, [b.title() for b in boroughs])
    ax.set_ylabel("Requests without coordinates (%)")
    ax.set_title("Missing coordinates by borough and split")
    ax.grid(axis="x", visible=False)
    ax.legend(ncols=3, loc="upper left")
    style.save(fig, CHARTS / "missing_coordinates_by_borough_split.png")

    fig, ax = plt.subplots(figsize=(7.5, 3.2))
    p = prec[order].div(prec[order].sum(axis=0), axis=1) * 100
    p = p.loc[[i for i in ["ADDRESS_POINT", "INTERSECTION", "BLOCKFACE", "PLACE", "UNKNOWN"] if i in p.index]]
    for k, s in enumerate(order):
        ax.barh(np.arange(len(p)) + (k - 1) * 0.26, p[s], 0.23, color=style.SPLIT_COLOR[s], label=s)
    ax.set_yticks(np.arange(len(p)), p.index)
    ax.invert_yaxis()
    ax.set_xlabel("Share of requests (%)")
    ax.set_title("Location precision (from historical address_type) by split")
    ax.grid(axis="y", visible=False)
    ax.legend(ncols=3, loc="lower right")
    style.save(fig, CHARTS / "location_precision_by_split.png")
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
