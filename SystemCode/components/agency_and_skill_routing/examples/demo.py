"""Integration walkthrough for team members (no CO1 application required).

``examples/inputs/``  hold CO1 messages: only the agreed CO1 fields, no agency.
``examples/outputs/`` hold what this component hands to duplicate detection.

Shows the four ways to call the component: a single dict, a batch, JSON text
in/out, and a stricter configuration with the audit detail block. Run:

    python examples/demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # allow running without installing the package

from civicops_agency import AgencySkillRouter, RouterConfig  # noqa: E402

CATALOG = ROOT / "data" / "reference" / "request_catalog.csv"
INPUTS = Path(__file__).resolve().parent / "inputs"


def load(name: str):
    return json.loads((INPUTS / name).read_text(encoding="utf-8"))


def main() -> None:
    router = AgencySkillRouter.from_catalog(CATALOG)

    # 1. One CO1 message as a dictionary. The input has no agency or skills;
    #    the router looks them up in the catalog from request_type.
    co1 = load("valid_co1.json")
    print("1) INPUT keys from CO1:", list(co1))
    result = router.assign(co1)
    print("   OUTPUT adds:", {k: v for k, v in result.to_dict().items() if k not in co1})

    # 2. A batch. Each item is independent; the malformed one becomes INVALID.
    print("\n2) Batch (results keep input order):")
    for i, r in enumerate(router.assign_batch(load("batch_co1.json")), start=1):
        print(f"   #{i} {str(r.request_type):<10} {r.status.value:<10} {str(r.agency):<6} next={r.next_step.value}")

    # 3. JSON text in, JSON text out (what a message queue would carry).
    raw = (INPUTS / "emergency_co1.json").read_text(encoding="utf-8")
    print("\n3) JSON in/out (emergency):")
    print(router.assign_json(raw))

    # 4. Stricter policy, and the optional audit detail block.
    strict = AgencySkillRouter.from_catalog(CATALOG, config=RouterConfig(min_confidence=0.8,
                                                                         review_on_elevated_referral_risk=True))
    r = strict.assign(load("referral_advisory_co1.json"))
    detail = r.to_dict(detail=True)["detail"]
    print("\n4) Strict config on RT043 ->", r.status.value, "| findings:", [f["code"] for f in detail["findings"]],
          "| rules:", detail["rules_applied"])


if __name__ == "__main__":
    main()
