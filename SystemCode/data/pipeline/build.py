"""Run the whole data pipeline, then write the manifest and the small committed sample.

    py -m data.pipeline.build              # download (skips files that exist), clean, synthetic, NLP
    py -m data.pipeline.build --skip-download

Outputs:
  data/processed/*.parquet   full tables (git-ignored, regenerate any time; deterministic with seed 2026)
  data/processed/manifest.json
  data/sample/*.csv          about 300 rows per borough plus every synthetic table (committed, under 5 MB),
                             so a teammate can run and test a component without downloading anything
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone

import pandas as pd

from . import aggregates, clean, download, nlp_data, synthetic
from .config import PROCESSED, RAW, SAMPLE, SCHEMA_VERSION, SEED


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def write_manifest() -> dict:
    entries = []
    for p in sorted(PROCESSED.glob("*.parquet")):
        df = pd.read_parquet(p)
        entries.append({"file": p.name, "rows": len(df), "columns": len(df.columns), "sha256_16": _sha(p),
                        "is_synthetic": p.name.startswith("synthetic_")})
    manifest = {"schema_version": SCHEMA_VERSION, "seed": SEED,
                "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "tables": entries}
    (PROCESSED / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def write_sample(per_borough: int = 300) -> None:
    SAMPLE.mkdir(parents=True, exist_ok=True)
    real = pd.read_parquet(PROCESSED / "requests_real.parquet")
    s = real.groupby("borough", group_keys=False).apply(lambda g: g.sample(min(per_borough, len(g)), random_state=SEED))
    s.to_csv(SAMPLE / "requests_real_sample.csv", index=False)
    links = pd.read_parquet(PROCESSED / "synthetic_reporter_links.parquet")
    links[links["request_id"].isin(s["request_id"])].to_csv(SAMPLE / "synthetic_reporter_links_sample.csv", index=False)
    cit = pd.read_parquet(PROCESSED / "synthetic_citizens.parquet")
    cit.to_csv(SAMPLE / "synthetic_citizens.csv", index=False)
    pd.read_parquet(PROCESSED / "synthetic_crews.parquet").to_csv(SAMPLE / "synthetic_crews.csv", index=False)
    pd.read_parquet(PROCESSED / "synthetic_urgency_scenarios.parquet").to_csv(SAMPLE / "synthetic_urgency_scenarios.csv", index=False)
    q = pd.read_parquet(PROCESSED / "synthetic_queue_snapshots.parquet")
    q[q["snapshot_id"].isin(q["snapshot_id"].unique()[:10])].to_csv(SAMPLE / "synthetic_queue_snapshots_sample.csv", index=False)
    nlp = pd.read_parquet(PROCESSED / "synthetic_nlp_utterances.parquet")
    nlp.groupby("intent", group_keys=False).apply(lambda g: g.sample(min(40, len(g)), random_state=SEED)) \
       .drop(columns=["tokens", "bio_tags"]).to_csv(SAMPLE / "synthetic_nlp_utterances_sample.csv", index=False)
    total = sum(p.stat().st_size for p in SAMPLE.glob("*.csv"))
    print(f"sample: {len(list(SAMPLE.glob('*.csv')))} files, {total / 1e6:.1f} MB in {SAMPLE}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--skip-download", action="store_true")
    a = ap.parse_args()
    if not a.skip_download:
        download.download_sample(); download.download_daily_counts(); download.download_weather()
    clean.main()
    if (RAW / "nyc311_daily_counts_2020_now.csv").exists():
        aggregates.main()
    else:
        print("WARNING: daily counts not downloaded yet; skipping aggregates (C13 tables). Run: py -m data.pipeline.download --only counts")
    synthetic.main()
    nlp_data.main()
    m = write_manifest()
    write_sample()
    for t in m["tables"]:
        print(f"  {t['file']:40} {t['rows']:>8,} rows  {'SYNTHETIC' if t['is_synthetic'] else 'real'}")


if __name__ == "__main__":
    main()
