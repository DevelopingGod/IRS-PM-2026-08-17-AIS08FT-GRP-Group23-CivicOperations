"""Brings Khalid's components into components/urgency_and_prioritisation/ at a pinned version.

His code lives on GitHub, on origin/khalid_code. A copy at the pinned commit is committed here,
so main holds every component and a fresh clone runs without this step. The exact versions
used are written to components/pins.json and shown on the tech team's health page. To use a
newer version, change the pin below on purpose, run this again and commit the result.

Mohammad's three packages have no branch of their own, so they are kept in git as he
delivered them. This script only records their versions.

Run: py -m app.backend.vendor_export
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess  # nosec B404: runs git with fixed arguments, command line only
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENTS = ROOT / "components"
KHALID_HOME = COMPONENTS / "urgency_and_prioritisation"

# Khalid's Urgency and Prioritisation, from his branch at this exact commit.
KHALID_REF = "906b77ae43fb646f29c6e5359d2e31cf5654c082"   # origin/khalid_code, 16 Oct 2026
KHALID_DIRS = ["components/C10_Urgency_Assignment", "components/C11_Request_Prioritization"]

MOHAMMAD = {
    "Agency and skill routing": (COMPONENTS / "agency_and_skill_routing", "civicops_agency"),
    "External factors": (COMPONENTS / "external_factors", "civicops_external_factors"),
    "Crew deployment and routing": (COMPONENTS / "crew_deployment_and_routing", "src/crew_dispatch"),
}


def git(*args: str) -> bytes:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, check=True).stdout  # nosec B603 B607


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def export_khalid() -> dict:
    files = []
    for d in KHALID_DIRS:
        out = KHALID_HOME / Path(d).name
        if out.exists():
            shutil.rmtree(out)
        for name in git("ls-tree", "-r", "--name-only", "--full-tree", KHALID_REF, d + "/").decode().splitlines():
            target = KHALID_HOME / Path(name).relative_to("components")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(git("show", f"{KHALID_REF}:{name}"))
            files.append(str(Path(name).relative_to("components")).replace("\\", "/"))
    return {"owner": "Khalid", "components": ["Urgency", "Prioritisation"],
            "source": "origin/khalid_code", "pinned": KHALID_REF[:7],
            "files": len(files), "path": str(KHALID_HOME.relative_to(ROOT)).replace("\\", "/")}


def pin_mohammad(name: str, home: Path, package: str) -> dict:
    init = home / package / "__init__.py"
    if not init.exists():
        return {"owner": "Mohammad", "components": [name], "pinned": None, "note": "folder not found"}
    version, stamp = "unknown", init
    for line in init.read_text(encoding="utf-8").splitlines():
        if line.startswith("__version__"):
            version = line.split("=")[1].strip().strip("\"'")
    if version == "unknown" and (home / "pyproject.toml").exists():
        # The Crew deployment package keeps its version in pyproject.toml, and its
        # PACKAGE_MANIFEST.json lists a hash of every file, so that manifest is its fingerprint.
        for line in (home / "pyproject.toml").read_text(encoding="utf-8").splitlines():
            if line.startswith("version"):
                version = line.split("=")[1].strip().strip("\"'")
        if (home / "PACKAGE_MANIFEST.json").exists():
            stamp = home / "PACKAGE_MANIFEST.json"
    return {"owner": "Mohammad", "components": [name], "source": "delivered package",
            "pinned": f"v{version}, {Path(package).name} {sha256(stamp)}",
            "path": str(home.relative_to(ROOT)).replace("\\", "/")}


def main() -> int:
    pins = {"exported_at": datetime.now().isoformat(timespec="seconds"),
            "khalid": export_khalid(),
            **{f"mohammad_{n.split()[0].lower()}": pin_mohammad(n, h, p) for n, (h, p) in MOHAMMAD.items()}}
    (COMPONENTS / "pins.json").write_text(json.dumps(pins, indent=2) + "\n", encoding="utf-8")
    for key, p in pins.items():
        if key != "exported_at":
            print(f"{p['owner']:<9} {', '.join(p['components']):<28} pinned {p['pinned']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
