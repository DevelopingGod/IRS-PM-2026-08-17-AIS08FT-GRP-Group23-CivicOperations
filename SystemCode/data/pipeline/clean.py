"""Step 2: turn the raw pull into one clean, AI-ready table of real requests.

Output: data/processed/requests_real.parquet, one row per real request, with
  * canonical fields named as in contracts.ServiceRequest
  * catalog mapping (request_type_code, complaint_family, required_skill, ...)
  * labels: resolution_hours (C09), outcome_class / verified_outcome (C06, C07)
  * features: time of day, weekday, holiday, weather of that day
  * split: train / val / test by TIME (no future leakage), shared by every component

Nothing here is synthetic. The synthetic reporter IDs are added in synthetic.py.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from pandas.tseries.holiday import USFederalHolidayCalendar

from .config import AGENCY_NAMES, PROCESSED, RAW, load_catalog, match_catalog

CHANNEL_MAP = {"ONLINE": "ONLINE", "MOBILE": "MOBILE", "PHONE": "PHONE", "OTHER": "OTHER"}

# Ordered rules, the first match wins. Built by reading the most frequent closing notes per
# agency (see 03_Research/notebooks/01_data_preparation.ipynb). It is a PROXY for "was the report genuine": it
# records what the crew found on arrival, not the citizen's intent.
OUTCOME_RULES: list[tuple[str, re.Pattern]] = [
    ("DUPLICATE", re.compile(r"duplicate|previously (filed|reported)|earlier complaint|already exists|"
                             r"addressed under another service request|already reported by another", re.I)),
    ("NO_ACCESS", re.compile(r"unable to gain entry|not able to gain access|could not gain access|denied access|"
                             r"unable to complete the inspection|could not access|could not locate the premises|"
                             r"not have sufficient location", re.I)),
    ("GENUINE", re.compile(r"maintenance unit for repair", re.I)),  # internal DOT hand-off = real defect
    ("REFERRED", re.compile(r"(?<!may be )referred|has requested the department|not fall under the jurisdiction|"
                            r"out of its jurisdiction|does not have jurisdiction|does not fall under|inside condition|"
                            r"exceeds the monetary value", re.I)),
    ("SUBSIDED", re.compile(r"temporary condition|had been restored|indicated that the condition was corrected|"
                            r"verified that the following conditions were corrected|"
                            r"those responsible for the condition were gone|owner claimed the vehicle|"
                            r"condition was corrected without the need", re.I)),
    ("UNFOUNDED", re.compile(r"no evidence|no violation|could not find|couldn'?t find|did not find|"
                             r"found no condition|not found|no criminal violation|no work is necessary|"
                             r"did not violate|no violations were issued|police action was not necessary|"
                             r"no further action was necessary|meets its standards|no sewer back ?up|"
                             r"no dsny related", re.I)),
    ("GENUINE", re.compile(r"repaired|corrected|completed the request|cleaned|collected|removed|issued a|"
                           r"violations were issued|summons|took action|shut the running|found violations|"
                           r"debris was obstructing|performed the work|work order|violation of law occurred|"
                           r"notice of violation|addressed the issue", re.I)),
]
VERIFIED = {"GENUINE": "GENUINE", "SUBSIDED": "GENUINE", "UNFOUNDED": "UNFOUNDED"}  # everything else INCONCLUSIVE


def outcome_class(text: str | float) -> str:
    if not isinstance(text, str) or not text.strip():
        return "NO_NOTE"
    for label, pat in OUTCOME_RULES:
        if pat.search(text):
            return label
    return "ADMINISTRATIVE"


def _fix_mojibake(s: pd.Series) -> pd.Series:
    return s.str.replace("â", "'", regex=False).str.replace("â", "'", regex=False)


def clean(raw: pd.DataFrame, weather: pd.DataFrame | None = None) -> pd.DataFrame:
    df = raw.copy()
    n0 = len(df)
    for col in ["complaint_type", "descriptor", "agency", "borough", "location_type", "incident_address",
                "address_type", "status", "resolution_description", "open_data_channel_type"]:
        if col in df:
            df[col] = df[col].astype("string").str.strip()
    df["resolution_description"] = _fix_mojibake(df["resolution_description"].fillna(""))
    df = df.drop_duplicates("unique_key")
    df = df[df["borough"].isin(["MANHATTAN", "BROOKLYN", "QUEENS", "BRONX", "STATEN ISLAND"])]

    # --- catalog mapping (drops rows whose descriptor is outside every catalog entry)
    catalog = load_catalog()
    hits = [match_catalog(ct, d, catalog) for ct, d in zip(df["complaint_type"], df["descriptor"].fillna(""))]
    df["request_type_code"] = [h.code if h else None for h in hits]
    df = df[df["request_type_code"].notna()].copy()
    by_code = {e.code: e for e in catalog}
    df["request_type_label"] = df["request_type_code"].map(lambda c: by_code[c].label)
    df["complaint_family"] = df["request_type_code"].map(lambda c: by_code[c].family)
    df["catalog_agency"] = df["request_type_code"].map(lambda c: by_code[c].agency)
    df["required_skill"] = df["request_type_code"].map(lambda c: by_code[c].skill)
    df["default_urgency"] = df["request_type_code"].map(lambda c: by_code[c].default_urgency)
    df["est_service_minutes"] = df["request_type_code"].map(lambda c: by_code[c].service_minutes)
    df["agency_name_std"] = df["agency"].map(AGENCY_NAMES).fillna(df.get("agency_name"))

    # --- time
    for col in ["created_date", "closed_date", "due_date", "resolution_action_updated_date"]:
        if col in df:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    df = df[df["created_date"].notna()]
    df["created_at"] = df["created_date"]
    df["hour"] = df["created_at"].dt.hour
    df["dow"] = df["created_at"].dt.dayofweek
    df["month"] = df["created_at"].dt.month
    df["is_weekend"] = df["dow"] >= 5
    hol = USFederalHolidayCalendar().holidays(df["created_at"].min(), df["created_at"].max())
    df["is_holiday"] = df["created_at"].dt.normalize().isin(hol)
    df["day"] = df["created_at"].dt.date

    # --- location
    df["latitude"] = pd.to_numeric(df["latitude"], errors="coerce")
    df["longitude"] = pd.to_numeric(df["longitude"], errors="coerce")
    df["geo_valid"] = df["latitude"].between(40.40, 41.00) & df["longitude"].between(-74.30, -73.60)
    df["incident_zip"] = df["incident_zip"].astype("string").str.extract(r"(\d{5})", expand=False)
    df["bbl"] = df.get("bbl", pd.Series(index=df.index, dtype="string")).astype("string")

    # --- channel
    df["channel"] = df["open_data_channel_type"].map(CHANNEL_MAP).fillna("UNKNOWN")

    # --- labels: resolution time (C09)
    hours = (df["closed_date"] - df["created_at"]).dt.total_seconds() / 3600
    df["resolution_hours"] = hours
    df["outcome_class"] = df["resolution_description"].map(outcome_class)
    df["verified_outcome"] = df["outcome_class"].map(VERIFIED).fillna("INCONCLUSIVE")
    df["resolution_valid"] = (
        df["status"].eq("Closed") & hours.between(0.0, 24 * 120)
        & ~df["outcome_class"].isin(["DUPLICATE", "ADMINISTRATIVE", "NO_NOTE"])
    )
    df["log_resolution_hours"] = np.where(df["resolution_valid"], np.log1p(hours.clip(lower=0)), np.nan)

    # --- weather on the day of filing (joins data/raw/weather_daily_2020_now.csv)
    if weather is not None and len(weather):
        w = weather.copy()
        w["day"] = pd.to_datetime(w["day"]).dt.date
        w = w.rename(columns={"precipitation_sum": "wx_precip_mm", "rain_sum": "wx_rain_mm",
                              "snowfall_sum": "wx_snow_cm", "temperature_2m_max": "wx_tmax_c",
                              "temperature_2m_min": "wx_tmin_c", "wind_gusts_10m_max": "wx_gust_kmh"})
        df = df.merge(w, on=["borough", "day"], how="left")

    # --- ids, provenance, time-based split
    df["request_id"] = "NYC-" + df["unique_key"].astype(str)
    df["source"] = "REAL_311"
    df["is_synthetic"] = False
    df = df.sort_values("created_at").reset_index(drop=True)
    q70, q85 = df["created_at"].quantile([0.70, 0.85])
    df["split"] = np.select([df["created_at"] <= q70, df["created_at"] <= q85], ["train", "val"], "test")
    print(f"clean: {n0:,} raw -> {len(df):,} catalog rows "
          f"({df['geo_valid'].mean():.1%} geo valid, {df['resolution_valid'].mean():.1%} usable for C09)")
    return df


def main() -> None:
    raw = pd.read_csv(RAW / "nyc311_sample_5boroughs.csv", dtype=str)
    wpath = RAW / "weather_daily_2020_now.csv"
    weather = pd.read_csv(wpath) if wpath.exists() else None
    df = clean(raw, weather)
    PROCESSED.mkdir(parents=True, exist_ok=True)
    df.drop(columns=["day"]).to_parquet(PROCESSED / "requests_real.parquet", index=False)
    print(df.groupby("borough").size().to_string())
    print(df["outcome_class"].value_counts().to_string())


if __name__ == "__main__":
    main()
