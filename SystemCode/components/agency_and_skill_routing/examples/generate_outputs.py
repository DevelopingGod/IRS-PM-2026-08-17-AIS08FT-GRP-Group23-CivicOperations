"""Regenerate ``examples/outputs/`` from ``examples/inputs/``.

``inputs/``  - what CO1 sends (only the agreed CO1 fields; no agency).
``outputs/`` - what this component hands to duplicate detection (slim
               contract). A test checks these files stay in sync with the code.

    python examples/generate_outputs.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_agency import AgencySkillRouter  # noqa: E402

INPUTS = ROOT / "examples" / "inputs"
OUTPUTS = ROOT / "examples" / "outputs"


def render(router: AgencySkillRouter, path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return json.dumps([r.to_dict() for r in router.assign_batch(data)], indent=2, ensure_ascii=False) + "\n"
    return router.assign(data).to_json() + "\n"


def main() -> None:
    router = AgencySkillRouter.from_catalog(ROOT / "data" / "reference" / "request_catalog.csv")
    OUTPUTS.mkdir(exist_ok=True)
    for path in sorted(INPUTS.glob("*.json")):
        (OUTPUTS / path.name).write_text(render(router, path), encoding="utf-8")
        print(f"examples/outputs/{path.name}")


if __name__ == "__main__":
    main()
