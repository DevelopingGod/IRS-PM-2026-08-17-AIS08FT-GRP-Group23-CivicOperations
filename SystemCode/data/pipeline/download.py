"""Step 1: pull the raw data. Everything is public and free, and no API key is needed.

A) Row-level sample: up to 10,000 real NYC 311 rows per borough, restricted to
   the request catalog, drawn as random time WINDOWS spread over the last 12
   months. Windows (not single random rows) keep reports of the same incident
   together, which the duplicate detector needs, while still covering every
   season and hour of day.
B) Daily counts per borough x complaint_type from 2020-01-01 to today, via
   server-side aggregation. Used by predictive alerting (C13) and as
   resolution-time baselines.
C) Daily weather history per borough centroid from the Open-Meteo archive.

Run:  py -m data.pipeline.download            (skips files that already exist)
      py -m data.pipeline.download --force
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from .config import (
    AGGREGATE_START, COLUMNS, RAW, ROWS_PER_BOROUGH, SAMPLE_MONTHS, SEED, SOCRATA,
    catalog_complaint_types, load_boroughs,
)


def _get(url: str, params: dict, attempts: int = 5) -> list | dict:
    full = url + "?" + urllib.parse.urlencode(params)
    for i in range(1, attempts + 1):
        try:
            with urllib.request.urlopen(full, timeout=180) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # network errors are retried, then re-raised with context
            if i == attempts:
                raise RuntimeError(f"GET failed after {attempts} attempts: {full[:200]}") from exc
            time.sleep(5 * i)
    raise AssertionError("unreachable")


def _in_clause(values: list[str]) -> str:
    return "(" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + ")"


def _soql_ts(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%S")


# --------------------------------------------------------------------------- A
def download_sample(force: bool = False) -> None:
    out = RAW / "nyc311_sample_5boroughs.csv"
    if out.exists() and not force:
        print(f"[A] exists, skipping: {out}")
        return
    rng = np.random.default_rng(SEED)
    types = _in_clause(catalog_complaint_types())
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0, tzinfo=None)
    start = end - timedelta(days=30 * SAMPLE_MONTHS)
    frames = []
    for borough in load_boroughs().borough:
        # size windows so each borough needs roughly 60 of them
        probe = _get(SOCRATA, {"$select": "count(*) as n",
                               "$where": f"borough='{borough}' AND complaint_type in {types} "
                                         f"AND created_date between '{_soql_ts(start)}' and '{_soql_ts(end)}'"})
        per_hour = max(int(probe[0]["n"]) / ((end - start).total_seconds() / 3600), 1e-3)
        window_h = int(np.clip(round(ROWS_PER_BOROUGH / 60 / per_hour), 1, 48))
        print(f"[A] {borough}: ~{per_hour:.0f} catalog rows/hour, window = {window_h} h")
        rows, used, seen_windows = [], 0, set()
        span_h = int((end - start).total_seconds() // 3600) - window_h
        while used < ROWS_PER_BOROUGH and len(seen_windows) < 2000:
            offset = int(rng.integers(0, span_h))
            if offset in seen_windows:
                continue
            seen_windows.add(offset)
            w0 = start + timedelta(hours=offset)
            w1 = w0 + timedelta(hours=window_h)
            page = _get(SOCRATA, {
                "$select": ",".join(COLUMNS),
                "$where": f"borough='{borough}' AND complaint_type in {types} "
                          f"AND created_date >= '{_soql_ts(w0)}' AND created_date < '{_soql_ts(w1)}'",
                "$order": "created_date", "$limit": "50000",
            })
            if not page:
                continue
            take = page[: ROWS_PER_BOROUGH - used]
            for r in take:
                r["sample_window_id"] = f"{borough[:3]}-{w0:%Y%m%d%H}"
            rows.extend(take)
            used += len(take)
        print(f"[A] {borough}: {used} rows from {len(seen_windows)} windows")
        frames.append(pd.DataFrame(rows))
    df = pd.concat(frames, ignore_index=True)
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[A] wrote {len(df):,} rows -> {out}")


# --------------------------------------------------------------------------- B
def download_daily_counts(force: bool = False) -> None:
    """Quarterly chunks, each cached in data/raw/daily_counts/, so a timeout only costs one chunk and a rerun resumes."""
    out = RAW / "nyc311_daily_counts_2020_now.csv"
    if out.exists() and not force:
        print(f"[B] exists, skipping: {out}")
        return
    chunk_dir = RAW / "daily_counts"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    types = _in_clause(catalog_complaint_types())
    today = date.today()
    for borough in load_boroughs().borough:
        for year in range(int(AGGREGATE_START[:4]), today.year + 1):
            for q in range(4):
                q0 = date(year, 3 * q + 1, 1)
                if q0 > today:
                    break
                q1 = date(year + (q == 3), (3 * q + 4) if q < 3 else 1, 1)
                f = chunk_dir / f"{borough.replace(' ', '_')}_{year}Q{q + 1}.csv"
                if f.exists() and not force and not (q0 <= today < q1):
                    continue
                page = _get(SOCRATA, {
                    "$select": "date_trunc_ymd(created_date) as day, complaint_type, count(*) as n",
                    "$where": f"borough='{borough}' AND complaint_type in {types} AND "
                              f"created_date >= '{q0}T00:00:00' AND created_date < '{q1}T00:00:00'",
                    "$group": "day, complaint_type", "$limit": "50000",
                })
                fr = pd.DataFrame(page, columns=["day", "complaint_type", "n"])
                fr["borough"] = borough
                fr.to_csv(f, index=False)
                print(f"[B] {borough} {year}Q{q + 1}: {len(fr):,} cells")
    df = pd.concat([pd.read_csv(f) for f in sorted(chunk_dir.glob("*.csv"))], ignore_index=True)
    df["day"] = pd.to_datetime(df["day"]).dt.date
    df["n"] = df["n"].astype(int)
    df.to_csv(out, index=False)
    print(f"[B] wrote {len(df):,} rows -> {out}")


# --------------------------------------------------------------------------- C
def download_weather(force: bool = False) -> None:
    out = RAW / "weather_daily_2020_now.csv"
    if out.exists() and not force:
        print(f"[C] exists, skipping: {out}")
        return
    frames = []
    end = (date.today() - timedelta(days=3)).isoformat()  # archive lags a few days
    for b in load_boroughs().itertuples():
        data = _get("https://archive-api.open-meteo.com/v1/archive", {
            "latitude": b.centroid_lat, "longitude": b.centroid_lon, "start_date": AGGREGATE_START,
            "end_date": end, "timezone": "America/New_York",
            "daily": "precipitation_sum,rain_sum,snowfall_sum,temperature_2m_max,temperature_2m_min,wind_gusts_10m_max",
        })
        f = pd.DataFrame(data["daily"]).rename(columns={"time": "day"})
        f["borough"] = b.borough
        frames.append(f)
        print(f"[C] {b.borough}: {len(f):,} days")
    df = pd.concat(frames, ignore_index=True)
    RAW.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[C] wrote {len(df):,} rows -> {out}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--only", choices=["sample", "counts", "weather"])
    a = ap.parse_args()
    steps = {"sample": download_sample, "counts": download_daily_counts, "weather": download_weather}
    for name, fn in steps.items():
        if a.only in (None, name):
            fn(force=a.force)


if __name__ == "__main__":
    main()
