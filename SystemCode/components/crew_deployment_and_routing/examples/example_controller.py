"""Event-driven integration example (what the CivicOps orchestrator / staff portal / Crew Portal would call).

Run from the component root:  python examples/example_controller.py
Uses the same sample inputs as example_propose_plan.py. Writes versioned plans, GeoJSON, an audit log
and maps to outputs/examples/controller_demo/ (deleted and recreated on each run).
"""
import json
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "src"))

from crew_dispatch.config import load_config  # noqa: E402
from crew_dispatch.dynamic.controller import DispatchController  # noqa: E402
from crew_dispatch.runtime import load_runtime  # noqa: E402
from crew_dispatch.schemas import Crew  # noqa: E402

cfg = load_config()                                      # config/default.yaml
router, catalog, _provider, _log = load_runtime(cfg)     # road network + A* router, request catalog

queue = json.loads((HERE / "sample_c11_output.json").read_text(encoding="utf-8"))   # C11 output, as given
crews = [Crew.from_record(c) for c in json.loads((HERE / "sample_crews.json").read_text(encoding="utf-8"))]
now = datetime.fromisoformat(json.loads((HERE / "sample_meta.json").read_text())["planning_time"])  # tz-aware

out = ROOT / "outputs" / "examples" / "controller_demo"
shutil.rmtree(out, ignore_errors=True)
ctl = DispatchController(crews, router, cfg, catalog, out, run_id="controller_demo", seed=0)
ctl.start_duty(now)                                      # duty periods from agency operating hours


def show(res):
    print(f"  -> replanned={res.replanned} published={res.published} version={res.plan_version} | {res.reason[:110]}")


# 1) Orchestrator delivers the latest C11 output
print("QUEUE_UPDATED (C11 output)")
show(ctl.handle({"type": "QUEUE_UPDATED", "time": now, "queue": queue, "trigger": "new_request", "source": "orchestrator"}))
crew_id, stops = next((c, s) for c, s in ctl.plan.routes.items() if s)
rid = stops[0]["request_id"]
print(f"   first planned job: {rid} -> {crew_id}")

# 2) Staff portal approves that job (only verified approvals lock work)
print("APPROVAL (staff portal)")
show(ctl.handle({"type": "APPROVAL", "time": now + timedelta(minutes=1), "request_id": rid, "crew_id": crew_id,
                 "verified": True, "approver": "supervisor-01", "approval_ref": "SP-0001", "source": "staff_portal"}))

# 3) Crew Portal status updates
print("JOB_STATUS EN_ROUTE / IN_PROGRESS (Crew Portal)")
show(ctl.handle({"type": "JOB_STATUS", "time": now + timedelta(minutes=2), "request_id": rid, "crew_id": crew_id,
                 "state": "EN_ROUTE", "source": "crew_portal"}))
show(ctl.handle({"type": "JOB_STATUS", "time": now + timedelta(minutes=12), "request_id": rid, "crew_id": crew_id,
                 "state": "IN_PROGRESS", "source": "crew_portal"}))

# 4) Periodic tick (orchestrator clock); reports jobs whose estimated finish passed without an update
print("TICK (+3 h, no completion reported yet)")
show(ctl.handle({"type": "TICK", "time": now + timedelta(hours=3), "source": "orchestrator"}))
print(f"   state of {rid}: {ctl.job_states[rid].value} (never auto-completed); awaiting: {ctl.awaiting_status_updates(now + timedelta(hours=3))}")

# 5) Crew reports completion
print("JOB_STATUS COMPLETED (Crew Portal)")
show(ctl.handle({"type": "JOB_STATUS", "time": now + timedelta(hours=3, minutes=5), "request_id": rid, "crew_id": crew_id,
                 "state": "COMPLETED", "actual_travel_min": 9, "actual_service_min": 75, "source": "crew_portal"}))

# 6) A crew becomes unavailable
other = next(c.crew_id for c in crews if c.crew_id != crew_id)
print(f"CREW_STATUS {other} unavailable")
show(ctl.handle({"type": "CREW_STATUS", "time": now + timedelta(hours=3, minutes=10), "crew_id": other, "available": False,
                 "source": "fleet_system"}))

print(f"\nplan versions: {sorted(p.name for p in (out / 'plans').glob('plan_v*.json'))}")
print(f"audit records: {sum(1 for _ in open(out / 'events' / 'events.jsonl', encoding='utf-8'))}   maps: {len(list((out / 'maps').glob('*.png')))}")
print(f"latest plan says dispatch_authorized = {ctl.doc['authorization']['dispatch_authorized']}")
