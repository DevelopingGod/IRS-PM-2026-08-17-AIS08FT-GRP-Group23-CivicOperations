"""Build the two distribution ZIPs.

    python make_dist.py [--out DIR]

* ``civicops_agency_runtime-<version>.zip``  - everything except the historical
  dataset (team members who only need the router).
* ``civicops_agency_research-<version>.zip`` - the same plus
  ``data/research/requests_real.parquet``.

Caches, virtual environments and editor folders are never included. Each ZIP
extracts into a single ``civicops_agency_component/`` folder.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TOP = "civicops_agency_component"
EXCLUDED_DIRS = {"__pycache__", ".pytest_cache", ".venv", "venv", ".git", ".idea", ".vscode", ".claude", "dist",
                 "build", "civicops_agency.egg-info"}
DATASET = Path("data/research/requests_real.parquet")


def files(include_dataset: bool):
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if path.is_dir() or any(part in EXCLUDED_DIRS or part.endswith(".egg-info") for part in rel.parts):
            continue
        if path.suffix in (".pyc", ".pyo"):
            continue
        if rel == DATASET and not include_dataset:
            continue
        yield path, rel


def build(out: Path, name: str, include_dataset: bool) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    target = out / name
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path, rel in files(include_dataset):
            zf.write(path, f"{TOP}/{rel.as_posix()}")
    return target


def main() -> None:
    import sys

    sys.path.insert(0, str(ROOT))
    from civicops_agency import __version__

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=ROOT.parent / "dist")
    args = parser.parse_args()
    for name, with_data in ((f"civicops_agency_runtime-{__version__}.zip", False),
                            (f"civicops_agency_research-{__version__}.zip", True)):
        z = build(args.out, name, with_data)
        with zipfile.ZipFile(z) as zf:
            count = len(zf.namelist())
        print(f"{z}  {z.stat().st_size / 1024:.0f} KB  {count} files")


if __name__ == "__main__":
    main()
