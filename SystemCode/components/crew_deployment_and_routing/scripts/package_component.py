"""Build the plug-and-play ZIP(s) of the component (run from the component root).

  python scripts/package_component.py             -> ../Crew_Dispatch_Route_Optimization.zip (full research package)
  python scripts/package_component.py --runtime   -> ../Crew_Dispatch_Route_Optimization_runtime.zip
     (integration package: code, config, enriched network, catalog, crew rosters, examples, tests, docs;
      no demo outputs, benchmark CSVs, replay data or source GraphML)

Includes source, tests, docs, config, data (road network source + enriched network, catalog, crews,
replay data), demo outputs, benchmark results and maps. Excludes the virtual environment, caches,
__pycache__ and scratch trial runs. Writes <component>/../Crew_Dispatch_Route_Optimization.zip and
a manifest with SHA-256 per file.
"""
import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXCLUDE_DIRS = {".venv", "cache", "__pycache__", ".pytest_cache", ".claude"}
EXCLUDE_PREFIXES = ("outputs/runs/trial_",)


RUNTIME_EXCLUDE = ("outputs/", "data/replay/", "data/road_network/source/nyc_drive.graphml")
RUNTIME = False


def included(p: Path) -> bool:
    rel = p.relative_to(ROOT).as_posix()
    if RUNTIME and rel.startswith(RUNTIME_EXCLUDE):
        return False
    if any(part in EXCLUDE_DIRS for part in p.relative_to(ROOT).parts):
        return False
    if rel.startswith(EXCLUDE_PREFIXES) or rel.endswith((".pyc", ".log")):
        return False
    return p.is_file()


def main(out: Path):
    files = sorted(p for p in ROOT.rglob("*") if included(p))
    manifest = []
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for p in files:
            rel = p.relative_to(ROOT).as_posix()
            z.write(p, f"Crew_Dispatch_Route_Optimization/{rel}")
            manifest.append({"path": rel, "bytes": p.stat().st_size, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()})
        z.writestr("Crew_Dispatch_Route_Optimization/PACKAGE_MANIFEST.json", json.dumps(
            {"files": len(manifest), "uncompressed_bytes": sum(m["bytes"] for m in manifest), "entries": manifest}, indent=1))
    print(f"{out}  files={len(manifest)}  uncompressed={sum(m['bytes'] for m in manifest) / 1e6:.1f} MB  zip={out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if a != "--runtime"]
    RUNTIME = "--runtime" in sys.argv
    name = "Crew_Dispatch_Route_Optimization_runtime.zip" if RUNTIME else "Crew_Dispatch_Route_Optimization.zip"
    main(Path(args[0]) if args else ROOT.parent / name)
