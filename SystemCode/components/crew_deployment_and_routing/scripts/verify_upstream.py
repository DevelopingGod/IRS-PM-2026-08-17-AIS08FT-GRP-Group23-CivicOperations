"""Record or verify SHA-256 hashes of the read-only upstream inputs.

Usage:
  python scripts/verify_upstream.py record   # writes data/provenance/upstream_hashes.json
  python scripts/verify_upstream.py verify   # compares current hashes with the recorded ones
Only the explicitly authorised files are listed; nothing else is read.
"""
import hashlib, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
RECORD = HERE / "data" / "provenance" / "upstream_hashes.json"
UPSTREAM = {
    "request_catalog": r"C:\Users\engha\Downloads\request_catalog.csv",
    "synthetic_crews": r"D:\NUS\Projects\IRS-Project-2026-KIMS\data\processed\synthetic_crews.parquet",
    "requests_real": r"D:\NUS\Projects\IRS-Project-2026-KIMS\data\processed\requests_real.parquet",
    "c11_prioritization": r"C:\Users\engha\Downloads\c11_prioritization.py",
    "c11_final": r"C:\Users\engha\Downloads\c11_final.py",
    "osm_graphml": r"D:\NUS\Projects\IRS-Project-2026-Agancy-Skills\civicops_shared_osm_roads\data\osm\roads\nyc_drive.graphml",
    "osm_boroughs": r"D:\NUS\Projects\IRS-Project-2026-Agancy-Skills\civicops_shared_osm_roads\data\osm\boundaries\nyc_boroughs.gpkg",
    "osm_coverage": r"D:\NUS\Projects\IRS-Project-2026-Agancy-Skills\civicops_shared_osm_roads\data\osm\boundaries\nyc_coverage.gpkg",
    "osm_manifest": r"D:\NUS\Projects\IRS-Project-2026-Agancy-Skills\civicops_shared_osm_roads\data\osm\roads_manifest.json",
    "osm_readme": r"D:\NUS\Projects\IRS-Project-2026-Agancy-Skills\civicops_shared_osm_roads\README.md",
}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def current():
    out = {}
    for key, p in UPSTREAM.items():
        path = Path(p)
        out[key] = {"path": p, "sha256": sha256(path) if path.exists() else None,
                    "size_bytes": path.stat().st_size if path.exists() else None}
    return out


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "verify"
    now = current()
    if mode == "record":
        RECORD.parent.mkdir(parents=True, exist_ok=True)
        RECORD.write_text(json.dumps(now, indent=2))
        print(f"recorded {len(now)} upstream hashes -> {RECORD}")
    else:
        before = json.loads(RECORD.read_text())
        bad = [k for k in before if before[k]["sha256"] != now.get(k, {}).get("sha256")]
        for k in before:
            print(f"{'CHANGED' if k in bad else 'unchanged':9s} {k}")
        sys.exit(1 if bad else 0)
