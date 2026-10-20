"""Create the distributable bundles in dist/.

  civicops_external_factors_v<ver>.zip   code + config + docs + examples + tests + report + outputs
                                         + RUNTIME OSM cache (facilities, boundaries, manifest): offline-ready
  civicops_osm_roads_<data_version>.zip  shared road network for Crew Dispatch (data/osm/roads/*)
  civicops_research_data.zip             historical requests + derived request-level research tables

Unpack the bundles into the same folder. Their paths line up (see OSM_DATA_README.md).
Usage: python tools/make_dist.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
TOP = "civicops_external_factors"

EXCLUDE_DIRS = {".venv", "__pycache__", ".pytest_cache", "dist", "_http_cache", ".claude"}
RESEARCH_ONLY = {"data/research/requests_real.parquet", "outputs/tables/request_distances_trainval.parquet",
                 "outputs/tables/request_distances_test.parquet", "outputs/tables/historical_decisions.parquet"}


def files_for_code() -> list[Path]:
    out = []
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file() or any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
            continue
        rel = p.relative_to(ROOT).as_posix()
        if rel in RESEARCH_ONLY or rel.startswith("data/osm/roads/") or rel.endswith(".pyc"):
            continue
        if rel.startswith("outputs/") and rel.endswith((".txt", ".log")):
            continue
        out.append(p)
    return out


def write_zip(name: str, paths: list[Path]) -> dict:
    DIST.mkdir(exist_ok=True)
    z = DIST / name
    with zipfile.ZipFile(z, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for p in paths:
            zf.write(p, f"{TOP}/{p.relative_to(ROOT).as_posix()}")
    h = hashlib.sha256(z.read_bytes()).hexdigest()
    return {"file": name, "files": len(paths), "size_mb": round(z.stat().st_size / 2**20, 2), "sha256": h}


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from civicops_external_factors import __version__
    man = json.loads((ROOT / "data/osm/manifest.json").read_text("utf-8"))
    info = [write_zip(f"civicops_external_factors_v{__version__}.zip", files_for_code())]
    roads = sorted((ROOT / "data/osm/roads").glob("*"))
    if roads:
        info.append(write_zip(f"civicops_osm_roads_{man['data_version']}.zip",
                              roads + [ROOT / "OSM_DATA_README.md", ROOT / "data/osm/manifest.json"]))
    research = [ROOT / r for r in sorted(RESEARCH_ONLY) if (ROOT / r).exists()]
    info.append(write_zip("civicops_research_data.zip", research))
    (DIST / "BUNDLES.json").write_text(json.dumps(info, indent=2), "utf-8")
    print(json.dumps(info, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
