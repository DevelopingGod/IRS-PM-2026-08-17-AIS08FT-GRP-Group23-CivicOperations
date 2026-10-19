# Integration contract — Crew Dispatch and Route Optimization (CivicOps AI)

Schema version of plan documents: `civicops-dispatch-plan/1.0`. Component version: `1.0.0`.

## 1. Position in the pipeline

```
intake → agency/skill assignment → coordinates → external factors → C10 urgency → C11 prioritisation
      → [Crew Dispatch & Route Optimization]  → staff portal (approval)  → Crew Portal (execution status)
```

Dispatch consumes **C11 output only**. It never recalculates urgency, never changes C11 fields, never
approves work. In live operation the CivicOps orchestrator calls C11 and passes its output to
Dispatch; Dispatch does not import C11. (The research replay calls C11 itself through
`upstream/c11_provider.py`, see §6.)

## 2. Input: C11 queue (list of dicts, as returned by `prioritize_from_c10(requests, now, root=...)`)

| Field | Required | Use in Dispatch |
|---|---|---|
| `request_id` | yes | identity (must be unique) |
| `agency` | yes | hard compatibility with crew agency |
| `c11_queue_status` | yes | `provisional` → eligible for proposals; `provisional_review` → excluded from the plan, listed under `pending_review`; `hold` → excluded, listed under `held` |
| `overall_queue_position` | yes for active | **sole priority input** (1 = first); `None` for held |
| `required_skill` (str) or `required_skills` (list) | yes* | hard skill coverage (crew skills ⊇ required). *Falls back to the catalog by `request_type_code` |
| `latitude`, `longitude` | yes* | snapped to the road graph; missing / > 500 m from the main strongly connected road component → `UNROUTABLE_LOCATION` |
| `est_service_minutes` | no | on-site duration; falls back to `request_catalog.csv`, then `capacity.default_service_min` (flagged in `notes`) |
| `request_type_code` | no | catalog fallback key |
| anything else | – | preserved verbatim, **never used for decisions** |

Explicitly ignored for optimisation (preserved only): `priority_band`, `urgency_level`,
`priority_score`, `c11_urgency`, `queue_position` (urgency-specific labels like `H2`), `agency_position`,
`dispatch_approved` (always `False` from C11; it neither excludes work from proposals nor counts as approval).
A static test (`tests/test_contracts_and_misc.py::test_no_forbidden_field_read_in_decision_code`) and a
behavioural test (scrambling those fields leaves the plan unchanged) enforce this.

Contract checks (`validate_c11_output`): unique ids; positions 1..n without gaps over active
requests; held requests last with `None`; `dispatch_approved` not `True`. Violations are logged
in the audit record; positions are still used as given.

## 3. Input: crews

```json
{"crew_id": "V2-DOT-QUE-01", "agency": "DOT", "skills": ["ROAD_REPAIR", "SIDEWALK_REPAIR"],
 "depot_lat": 40.72, "depot_lon": -73.84, "work_time_budget_min": 420}
```
`current_latitude/current_longitude` are accepted as aliases of the depot when no depot is given.
Skills must use the catalog vocabulary (checked by tests for the generated rosters).

Dynamic crew state (`CrewStatus`): `available`, current `lat/lon`, duty period
(`duty_start/duty_end`, from the agency operating hours), `worked_min` (confirmed productive minutes),
ordered `locked` jobs (`LockedJob`: request, state, location, service, estimated travel, `since`,
`approval_ref`).

## 4. Execution states (Dispatch-owned, separate from `c11_queue_status`)

`UNASSIGNED → PLANNED → APPROVED_NOT_STARTED → EN_ROUTE → IN_PROGRESS → COMPLETED`, plus
`PENDING_REVIEW`, `HELD`, `CANCELLED`. Locked = `APPROVED_NOT_STARTED`, `EN_ROUTE`, `IN_PROGRESS`.
Allowed transitions are enumerated in `schemas.ALLOWED_TRANSITIONS`; a locked job can return to
`UNASSIGNED` only through an authoritative `STAFF_OVERRIDE` (or Crew Portal cancellation).

## 5. Events accepted by `DispatchController.handle(event)`

All events: `{"type", "time": tz-aware datetime, "source", "simulated": bool}`.

| type | payload | effect |
|---|---|---|
| `QUEUE_UPDATED` | `queue` (C11 output), `trigger` | state sync, change detection, replan decision |
| `TICK` | – | periodic replan check; reports jobs awaiting a status update |
| `APPROVAL` | `request_id`, `crew_id`, `approver`, `verified`, `approval_ref` | **only verified approvals** lock a planned job (`APPROVED_NOT_STARTED`) |
| `JOB_STATUS` | `request_id`, `crew_id`, `state` ∈ {EN_ROUTE, IN_PROGRESS, COMPLETED, CANCELLED}, `actual_travel_min`, `actual_service_min`, `est_travel_min` | Crew Portal updates. `EN_ROUTE` requires a prior approval; `COMPLETED` only from `IN_PROGRESS` of the same crew |
| `CREW_STATUS` | `crew_id`, `available`, `lat/lon` | breakdown / return; forced replan if the crew had planned work |
| `STAFF_OVERRIDE` | `request_id`, `action="release_lock"`, `approver` | documented staff intervention releasing a lock |

### Future Crew Portal contract
The Crew Portal sends `JOB_STATUS` events. Dispatch never marks a job completed because its estimate
elapsed: `awaiting_status_updates(now)` lists overdue jobs, the TICK audit record names them, and
planning assumes the crew is busy until `now + dynamic.overdue_buffer_min`. On `COMPLETED`, the
crew's `worked_min` increases by the reported actual minutes, its position moves to the job site
and a replan is considered (forced if the actual time deviated from the estimate by ≥ the buffer).
In the research replay every such event carries `"simulated": true` and
`"source": "simulated_crew_portal"`.

### Future staff-portal (authorisation) contract
Dispatch runs in **proposal-only mode** (`authorization.dispatch_authorized = false` in every plan).
The staff portal approves individual planned jobs by sending a verified `APPROVAL`. Unverified
approvals, or approvals for a crew other than the planned one, are rejected and logged.

## 6. Obtaining C11 output in a shared root

```
CivicOps_AI/
  C10_Urgency_Assignment/
  C11_Prioritization/            c11_final.py (+ c11_prioritization.py, c11_sla_lookup.py,
                                 c11_category_enrichment.py, nyc311_official_sla.csv,
                                 c11_water_sewer_SLA_detail_review.csv)
  Crew_Dispatch_Route_Optimization/
```
`upstream.c11_path` (default `../C11_Prioritization`, env `CIVICOPS_C11_PATH`) and
`upstream.c11_entry` (`c11_final:prioritize_from_c10`) locate it. Bytecode writing is disabled
so no `__pycache__` is created in upstream folders. If it cannot be loaded, the documented fallback
is the vendored, hash-checked, unmodified `c11_prioritization.py` (catalog `default_urgency` passed as
the C10 stand-in input; no SLA points; positions numbered from C11's returned order). Each plan
records the provider, path, file hashes and the proxy inputs used (`c11_source`).

## 7. Public functions

```python
from crew_dispatch.api import propose_plan          # one-shot proposal from a C11 queue -> plan document (dict)
propose_plan(c11_output, crews, now, crew_status=None, previous_assignment=None, previous_routes=None,
             job_states=None, seed=0, method="ga", config_path=None, run_id="adhoc", version=1)

from crew_dispatch.engine import optimize_plan       # stateless engine -> PlanResult
optimize_plan(snapshot, router, cfg, catalog, seed=0, method="ga")   # method: ga | rank_first_greedy | travel_aware_greedy | exact | warm_insertion

from crew_dispatch.dynamic.controller import DispatchController     # stateful, event-driven
ctl = DispatchController(crews, router, cfg, catalog, run_dir, run_id="...", seed=0)
ctl.start_duty(now); ctl.handle(event) -> ControllerResult
```

Errors: `SchemaError` (invalid crew record, duplicate request ids, naive datetimes); `ValueError` for
illegal state transitions or unknown events. A request with invalid data never crashes planning; it
receives reason `INVALID_INPUT` with a note.

## 8. Output: plan document (`outputs/.../plans/plan_v####.json`, immutable)

Top-level keys: `schema_version, run_id, plan_version, plan_id, authorization, event, planning_time,
data_versions, c11_source, algorithm (method, seed, objective, ga, routing, capacity, operating_hours),
metrics, metrics_by_agency, crews, assignments, unassigned, pending_review, held, locked_commitments,
locked_conflicts, changes_from_previous, route_geometry_file, diagnostics, requests`.

* `requests[i] = {"request": <upstream record verbatim>, "dispatch": {execution_state, c11_queue_status,
  overall_queue_position_used, rank_weight, crew_id, sequence, reasons, notes, snap_distance_m, derived_inputs}}`
* `crews[i]`: availability, duty end, budget, worked, reserved-by-locked, capacity for new work,
  planned work/travel, remaining after plan, locked commitments, jobs awaiting update, and
  `planned_stops` (sequence, `est_start`, `est_finish`, `travel_in_min`, `service_min`,
  `leg_distance_m`, `leg_astar_min`, return leg).
* `unassigned[i].reasons` ⊆ {`HELD_BY_C11`, `PENDING_HUMAN_REVIEW`, `UNROUTABLE_LOCATION`,
  `NO_COMPATIBLE_CREW`, `NO_AVAILABLE_COMPATIBLE_CREW`, `INSUFFICIENT_TIME_CAPACITY`,
  `CAPACITY_USED_BY_OTHER_REQUESTS`, `DEFERRED_BY_OPTIMIZER`, `INVALID_INPUT`,
  `ALREADY_COMPLETED_OR_CANCELLED`} — categories, not causal proofs.
* `changes_from_previous`: newly assigned, no longer assigned, moved to another crew, resequenced crews.
* `routes_v####.geojson`: LineStrings for every leg (`kind` = `leg` | `return_to_depot` | `locked_leg`).
* `events/events.jsonl`: one audit record per event (simulated flag, replan decision, objective values).

## 9. Handoff summary

**Upstream must supply**: C11 output with the fields of §2 (including coordinates and the skill),
crew records (§3), Crew Portal status events and verified staff approvals (§5).
**Downstream may consume**: plan documents (§8) as *proposals*, GeoJSON for map layers, the audit
log, and static PNG maps (`maps/`). Nothing in the output authorises dispatch.
