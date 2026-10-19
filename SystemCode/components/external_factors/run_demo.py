"""One-command demo: validate the local OSM cache, evaluate the synthetic examples, run a batch.

Usage:  python run_demo.py
Runs offline (no internet needed) once data/osm contains the runtime cache.
"""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from civicops_external_factors import CriticalFacilityEvaluator, OSMCacheError  # noqa: E402
from civicops_external_factors.osm_loader import validate_cache_files  # noqa: E402


def main() -> int:
    rep = validate_cache_files(ROOT / "data" / "osm", verify_checksums=True)
    print("OSM cache check:", "OK" if rep["ok"] else "PROBLEM", "| missing:", rep["missing_runtime_files"] or "none")
    try:
        ev = CriticalFacilityEvaluator.from_config(ROOT / "config" / "proximity_rules.yaml")
    except OSMCacheError as exc:
        print("Cannot start:", exc)
        print("Unpack the shared OSM data bundle into data/osm or run: python osm_data_prep/download_osm.py")
        return 3
    runpy.run_path(str(ROOT / "examples" / "demo.py"), run_name="__main__")
    lines = (ROOT / "examples" / "batch_requests.jsonl").read_text("utf-8").splitlines()
    results = ev.evaluate_batch([json.loads(x) for x in lines if x.strip()])
    print("\nBatch of", len(results), "synthetic requests:")
    for r in results:
        print(f"  {r.request_id:<18} factor={str(r.external_factor):<5} status={r.evaluation_status:<10} "
              f"reason={r.incomplete_reason or '-':<38} warnings={r.warnings[:2]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
