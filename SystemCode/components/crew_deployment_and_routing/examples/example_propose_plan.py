"""Minimal integration example: C11 output + crews -> proposed plan document.

Run from the component root:  python examples/example_propose_plan.py
Inputs : examples/sample_c11_output.json (C11-shaped records for 15 real NYC311 requests of one
         test window, prioritised by the vendored C11 fallback) and examples/sample_crews.json.
Output : examples/sample_plan.json (proposal only; authorization.dispatch_authorized == False).
"""
import json
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "src"))

from crew_dispatch.api import propose_plan  # noqa: E402
from crew_dispatch.outputs import dumps  # noqa: E402

queue = json.loads((HERE / "sample_c11_output.json").read_text(encoding="utf-8"))
crews = json.loads((HERE / "sample_crews.json").read_text(encoding="utf-8"))
now = datetime.fromisoformat(json.loads((HERE / "sample_meta.json").read_text())["planning_time"])
doc = propose_plan(queue, crews, now, seed=0)
(HERE / "sample_plan.json").write_text(dumps(doc), encoding="utf-8")
m = doc["metrics"]
print(f"served {int(m['served'])}/{m['provisional_candidates']} eligible, travel {m['travel_min']:.0f} min, "
      f"pending review {doc['pending_review']}, held {doc['held']}")
for c in doc["crews"]:
    if c["planned_stops"]:
        print(c["crew_id"], [s["request_id"] for s in c["planned_stops"]])
