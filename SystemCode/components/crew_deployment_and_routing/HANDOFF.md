# Handoff guide — Crew Dispatch and Route Optimization

For teammates who integrate this component into CivicOps AI, or who want to test it.
Details: `research/integration_contract.md`. Results: `research/report.html`.

## 1. What to share

| Package | File | Size | Give it to |
|---|---|---|---|
| **Runtime (plug-and-play)** | `Crew_Dispatch_Route_Optimization_runtime.zip` | ~10 MB | pipeline / UI integrators, testers |
| **Full research package** | `Crew_Dispatch_Route_Optimization.zip` | ~134 MB | report writers, examiners, anyone re-running experiments |

Both contain the same code, config, enriched road network (`data/road_network/derived/nyc_drive_enriched.npz`),
catalog, crew rosters, examples, tests and documentation. The full package adds: demo outputs and maps,
benchmark CSVs, replay data (historical windows), the 77 MB source GraphML (only needed to *rebuild* the
enriched network) and the HTML report.

**Do not share** the `.venv` folder (machine-specific) or anything in `cache/`.

Requirements: Python 3.11 or newer (tested on 3.14, Windows 11), about 300 MB for the virtual environment,
no GPU, no internet after installation (maps use the local road graph, not online tiles).

## 2. Setup (one time)

Unzip, open a terminal **in the `Crew_Dispatch_Route_Optimization` folder**, then:

Windows (PowerShell)
```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
$env:PYTHONPATH = "src"
```
macOS / Linux
```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export PYTHONPATH=src
```
(Commands below use `python`; on Windows that means `.venv\Scripts\python`, on macOS/Linux `.venv/bin/python`.)

## 3. Check it works (about 1 minute)

```bash
python -m pytest -q                         # expect: all tests pass (a few skip in the runtime package)
python examples/example_propose_plan.py     # one-shot API on 15 real NYC311 requests
python examples/example_controller.py       # event-driven API: approval, Crew Portal updates, breakdown
```
Expected from the first example: `served 8/13 eligible ... pending review ['NYC-70321961'], held ['NYC-70324527']`
followed by three crew routes. The second prints each event's decision and ends with
`dispatch_authorized = False`.

## 4. Using the API

### 4a. One-shot proposal (simplest)

```python
from datetime import datetime
from zoneinfo import ZoneInfo
from crew_dispatch.api import propose_plan

now = datetime.now(ZoneInfo("America/New_York"))           # must be timezone-aware
doc = propose_plan(c11_output, crews, now)                  # returns a dict (plan document)
```
* `c11_output`: the list returned by C11 `prioritize_from_c10(...)`, passed **unchanged**.
* `crews`: list of dicts (or `Crew` objects).
* The first call loads the road network (a few seconds); later calls reuse it.

### 4b. Event-driven (rolling replanning) — for the orchestrator, staff portal and Crew Portal

```python
from crew_dispatch.config import load_config
from crew_dispatch.runtime import load_runtime
from crew_dispatch.dynamic.controller import DispatchController
from crew_dispatch.schemas import Crew

cfg = load_config()
router, catalog, _, _ = load_runtime(cfg)
ctl = DispatchController([Crew.from_record(c) for c in crews], router, cfg, catalog,
                         "outputs/live", run_id="live-2026-10-19")
ctl.start_duty(now)
ctl.handle({"type": "QUEUE_UPDATED", "time": now, "queue": c11_output, "trigger": "new_request", "source": "orchestrator"})
```
Send these events as they happen (all need a timezone-aware `time`):

| Who sends it | Event | Required fields |
|---|---|---|
| Orchestrator, after every C11 run | `QUEUE_UPDATED` | `queue` (C11 output), `trigger` |
| Orchestrator clock (e.g. every few minutes) | `TICK` | – |
| Staff portal, when a supervisor approves a planned job | `APPROVAL` | `request_id`, `crew_id`, `approver`, `verified: true`, `approval_ref` |
| Crew Portal | `JOB_STATUS` | `request_id`, `crew_id`, `state`: `EN_ROUTE` → `IN_PROGRESS` → `COMPLETED` (or `CANCELLED`); optional `actual_travel_min`, `actual_service_min` |
| Fleet / supervisor | `CREW_STATUS` | `crew_id`, `available` (true/false), optional `lat`, `lon` |
| Supervisor override | `STAFF_OVERRIDE` | `request_id`, `action: "release_lock"`, `approver` |

`handle()` returns `published`, `plan_version`, `reason` and `changes`. The current plan is `ctl.doc`
(dict). Each published version is also written to `outputs/live/plans/plan_v####.json`, with route
geometry `routes_v####.geojson`, the audit log `events/events.jsonl` and maps in `maps/`.

### 4c. Input fields

C11 record (fields Dispatch reads; everything else is kept but ignored):

| Field | Note |
|---|---|
| `request_id` | unique |
| `agency` | e.g. `DOT` (catalog codes) |
| `c11_queue_status` | `provisional` (plannable), `provisional_review` (listed, not planned), `hold` (listed, not planned) |
| `overall_queue_position` | 1, 2, 3… for active requests, `None` for held. **The only priority input** |
| `required_skill` (or `required_skills` list) | catalog vocabulary, e.g. `ROAD_REPAIR` |
| `latitude`, `longitude` | missing, or more than 500 m from a road → `UNROUTABLE_LOCATION` |
| `est_service_minutes` | optional; otherwise taken from the catalog via `request_type_code` |

Crew record:
```json
{"crew_id": "DOT-QN-01", "agency": "DOT", "skills": ["ROAD_REPAIR"],
 "depot_lat": 40.72, "depot_lon": -73.84, "work_time_budget_min": 420}
```

### 4d. Reading the plan document

| Key | Meaning |
|---|---|
| `crews[i].planned_stops` | ordered stops: `request_id`, `est_start`, `est_finish`, `travel_in_min`, `service_min`, distances |
| `assignments` | flat list: request → crew, sequence, times |
| `unassigned[i].reasons` | why a request is not planned (capacity, no compatible crew, unroutable, …) |
| `pending_review`, `held`, `locked_commitments` | lists of request ids |
| `requests[i].request` / `requests[i].dispatch` | original C11 record unchanged / what Dispatch added |
| `changes_from_previous` | what changed since the last version |
| `authorization.dispatch_authorized` | always `false`: proposals only |

## 5. Rules integrators must keep

1. Pass C11 output **unchanged**. Dispatch never reads urgency, band or score, and never edits C11 fields.
2. Every plan is a **proposal**. Only a verified `APPROVAL` from the staff portal locks a job; Dispatch never
   approves anything itself.
3. A job is **completed only** when the Crew Portal sends `JOB_STATUS: COMPLETED`. An elapsed estimate is only
   reported as "awaiting status update".
4. Jobs that are approved, en route or in progress are **never re-planned** automatically. To release one,
   send `STAFF_OVERRIDE`.
5. All times must be **timezone-aware** (`America/New_York`); naive datetimes are rejected.

## 6. Where C11 lives (shared root)

Place the folders side by side:
```
CivicOps_AI/
  C10_Urgency_Assignment/
  C11_Prioritization/                  (c11_final.py and its modules / CSVs)
  Crew_Dispatch_Route_Optimization/
```
In live use the orchestrator calls C11 and passes its output to Dispatch, so Dispatch does not need C11 at
all. Only the research replay (`replay`, `demo`, `experiment`) calls C11 itself: it loads
`../C11_Prioritization/c11_final.py` (override with the environment variable `CIVICOPS_C11_PATH`), and if that
folder is missing it falls back to the bundled, unmodified `c11_prioritization.py`. The fallback is logged in
every plan under `c11_source`.

## 7. Testing guide

| What | Command |
|---|---|
| Everything | `python -m pytest -q` |
| Routing (A*, ALT, Dijkstra, one-way streets, snapping, speeds) | `python -m pytest tests/test_routing.py tests/test_real_network.py -q` |
| Optimiser rules (priority input, eligibility, skills, capacity, reasons) | `python -m pytest tests/test_engine.py tests/test_optimizer.py -q` |
| Dynamic rules (locks, approvals, completions, breakdown, versioning) | `python -m pytest tests/test_dynamic.py -q` |
| Contracts, maps, CPU-only | `python -m pytest tests/test_contracts_and_misc.py -q` |

To write a new test without NYC data, use the synthetic grid in `crew_dispatch.fixtures`
(`grid_network()`, `c11_record(...)`, `crew_record(...)`); see `tests/conftest.py` for ready-made fixtures.

Command-line runs on the historical replay data (**full package only**; the runtime package has no replay data,
so there you test through the two examples and the API):

```bash
python -m crew_dispatch plan --episode MAN-2026090612__test --roster cf1.0     # static plan + maps -> outputs/runs/
python -m crew_dispatch replay --episode STA-2026082208__test --roster cf0.8   # dynamic replay (simulated execution)
python -m crew_dispatch demo                                                    # all four demonstrations
python scripts/build_report.py                                                  # regenerate research/report.html
```

## 8. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| `ModuleNotFoundError: crew_dispatch` | set `PYTHONPATH=src` (step 2) or run from the component folder |
| `SchemaError: snapshot.now must be timezone-aware` | pass `datetime(..., tzinfo=ZoneInfo("America/New_York"))` |
| Request listed with `UNROUTABLE_LOCATION` | coordinates missing or more than 500 m from the road network |
| `NO_COMPATIBLE_CREW` | no crew of that agency has the required skill; check the crew `skills` list |
| Plan has `c11_source.provider = vendored ... (fallback)` | the shared `C11_Prioritization` folder was not found (only matters for replay) |
| First call takes a few seconds | loading the road network once; later calls are fast |
