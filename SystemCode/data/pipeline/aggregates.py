"""Step 2b: daily demand tables (real data, 2020 onward) for predictive alerting (C13).

Output:
  data/processed/daily_demand_weather.parquet   borough x family x day counts joined with weather (training table for C13)
  data/reference/demand_baselines.csv           mean daily count per borough x family x weekday on dry, mild days
                                                 (the "normal weather" baseline the alert lift is measured against)
"""
from __future__ import annotations

import pandas as pd

from .config import PROCESSED, RAW, REFERENCE, load_catalog


def main() -> None:
    counts = pd.read_csv(RAW / "nyc311_daily_counts_2020_now.csv")
    fam = {ct: e.family for e in load_catalog() for ct in e.complaint_types}
    counts["complaint_family"] = counts["complaint_type"].map(fam)
    daily = counts.groupby(["borough", "day", "complaint_family"], as_index=False)["n"].sum()
    daily["day"] = pd.to_datetime(daily["day"])
    w = pd.read_csv(RAW / "weather_daily_2020_now.csv", parse_dates=["day"])
    w = w.rename(columns={"precipitation_sum": "wx_precip_mm", "rain_sum": "wx_rain_mm", "snowfall_sum": "wx_snow_cm",
                          "temperature_2m_max": "wx_tmax_c", "temperature_2m_min": "wx_tmin_c",
                          "wind_gusts_10m_max": "wx_gust_kmh"})
    df = daily.merge(w, on=["borough", "day"], how="left")
    df["dow"] = df["day"].dt.dayofweek
    df["month"] = df["day"].dt.month
    df.to_parquet(PROCESSED / "daily_demand_weather.parquet", index=False)
    calm = df[(df["wx_precip_mm"].fillna(0) < 2) & (df["wx_snow_cm"].fillna(0) == 0) & (df["wx_gust_kmh"].fillna(0) < 40)]
    base = calm.groupby(["borough", "complaint_family", "dow"], as_index=False)["n"].mean().rename(columns={"n": "mean_daily"})
    base["mean_daily"] = base["mean_daily"].round(2)
    base.to_csv(REFERENCE / "demand_baselines.csv", index=False)
    print(f"daily_demand_weather: {len(df):,} rows; demand_baselines: {len(base)} rows")


if __name__ == "__main__":
    main()
