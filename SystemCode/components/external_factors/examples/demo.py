"""Demo: evaluate the SYNTHETIC example requests offline and show the Urgency hand-off.

Usage (from the package root):  python examples/demo.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from civicops_external_factors import CriticalFacilityEvaluator, to_integer_factor  # noqa: E402


def main() -> None:
    ev = CriticalFacilityEvaluator.from_config(ROOT / "config" / "proximity_rules.yaml")
    print(f"Ruleset {ev.ruleset.ruleset_version} | mode {ev.ruleset.decision_mode} | thresholds {ev.ruleset.thresholds_m}")
    print(f"OSM data {ev.cache.data_version} | {len(ev.engine.facilities):,} facilities indexed (offline)\n")
    for name in ("near_facility.json", "no_nearby_facility.json", "uncertain_location.json", "missing_coordinates.json"):
        req = json.loads((ROOT / "examples" / name).read_text("utf-8"))
        r = ev.evaluate(req)
        print(f"== {name}  ->  external_factor = {r.external_factor}  ({r.evaluation_status})")
        print("   " + r.explanation)
        if r.triggering_facilities:
            f = r.triggering_facilities[0]
            print(f"   evidence: {f.facility_category} '{f.name}' {f.distance_m:.1f} m (osm {f.facility_id})")
        print("   urgency payload:", json.dumps(r.to_urgency_payload())[:240], "...\n")
    # Integer-only adapter: requires an explicitly agreed null policy.
    r = ev.evaluate(json.loads((ROOT / "examples" / "missing_coordinates.json").read_text("utf-8")))
    try:
        to_integer_factor(r)
    except ValueError as exc:
        print("Integer adapter without policy ->", exc)
    print("Integer adapter with ZERO_FLAGGED ->", to_integer_factor(r, "ZERO_FLAGGED"))


if __name__ == "__main__":
    main()
