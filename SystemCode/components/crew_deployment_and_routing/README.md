# CivicOps AI — Crew Dispatch and Route Optimization

A self-contained research component that turns the **C11 prioritised queue** into a **proposed**
crew dispatch plan for municipal service requests (NYC311). It answers: which requests are
assigned, to which crew, in what order, along which roads, with what estimated times, and which
requests stay in the backlog and why. It replans as events arrive while never touching committed
work.

* **Evolutionary computing**: a genetic algorithm assigns and sequences jobs (rank-aware,
  capacity-constrained, stability-aware). See [research/methodology.md](research/methodology.md).
* **Informed search**: A\* (straight-line and landmark/ALT heuristics, both proven admissible and
  checked against Dijkstra) on a locally enriched OpenStreetMap drive network gives road-following
  routes and travel times.
* **Priority input**: only C11's `overall_queue_position`. Urgency, band and score are preserved
  but never read. **Proposal only**: neither C11 nor this component authorises dispatch.

## 1. Installation (CPU only)

```bash
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```
(Linux/macOS: `.venv/bin/python`.) Python ≥ 3.11 (tested on 3.14, Windows 11). No GPU, CUDA or
internet access is needed: maps use the local road graph, not online tiles. GPU acceleration was
deliberately not implemented (see decision D-17): the per-agency problems are small and the GA work
is branchy Python, so CPU is the right target.

Run commands from this folder with `src` on the path, e.g. `set PYTHONPATH=src` (PowerShell:
`$env:PYTHONPATH="src"`), or `pip install -e .` once and use the `crew-dispatch` command.

## 2. Inputs

| Input | File | Notes |
|---|---|---|
| Road network (copied, read-only original untouched) | `data/road_network/source/nyc_drive.graphml` (+ boundaries, manifest) | © OpenStreetMap contributors, ODbL |
| Enriched network used at runtime | `data/road_network/derived/nyc_drive_enriched.npz` | rebuilt with `prepare-network` |
| Request catalog | `data/catalog/request_catalog.csv` | agency / skill vocabulary, service minutes |
| Crew rosters | `data/crews/crews_v2_cf{0.6,0.8,1.0,1.2}.parquet`, `crews_legacy_v1_budget.parquet` | synthetic (see data provenance) |
| Historical replay windows | `data/replay/requests_windows.parquet`, `episodes.parquet` | input-safe columns only |
| C11 | live: caller passes C11 output; replay: `../C11_Prioritization/c11_final.py` or the vendored fallback | see integration contract §6 |

## 3. Commands

```bash
python -m crew_dispatch prepare-network                     # rebuild + validate the enriched network (~40 s)
python -m crew_dispatch prepare-data --requests <requests_real.parquet>   # replay episodes + crews_v2 rosters
python -m crew_dispatch plan   --episode MAN-2026090612__test --roster cf1.0          # static proposal + maps
python -m crew_dispatch replay --episode STA-2026082208__test --roster cf0.8 --policy hybrid --interval 15
python -m crew_dispatch demo                                 # D1-D4 demonstrations -> outputs/demos/
python -m crew_dispatch experiment <tuning|static|dynamic|speed|budget|exact_gap|routing>
python -m crew_dispatch verify-upstream                      # SHA-256 check of the authorised upstream files
python -m pytest -q                                          # test suite (synthetic fixtures + real-network checks)
```

Python API for the pipeline:

```python
from crew_dispatch.api import propose_plan
doc = propose_plan(c11_output, crews, now)        # plan document (dict), authorization.dispatch_authorized == False
```
Event-driven use: `crew_dispatch.dynamic.controller.DispatchController` (`handle(event)` for C11 queue
updates, ticks, staff approvals, Crew Portal status updates, crew availability and staff overrides).

## 4. Outputs and how to read them

```
outputs/<runs|demos>/<run_id>/
  plans/plan_v0001.json        immutable plan versions (never overwritten)
  plans/routes_v0001.geojson   road-following legs per crew (leg | return_to_depot | locked_leg)
  events/events.jsonl          one audit record per event (simulated flag, replan decision, objective values)
  maps/plan_v0001_<AGENCY>.png static route map per agency; compare_vA_vB_<AGENCY>.png before/after
outputs/benchmarks/            experiment CSVs + analysis/ (tables, statistics, figures)
```

In a plan document, `requests[i].request` is the upstream C11 record verbatim and
`requests[i].dispatch` is what Dispatch added: execution state, crew, sequence, rank weight, and the
**reasons** a request is not planned (`HELD_BY_C11`, `PENDING_HUMAN_REVIEW`, `UNROUTABLE_LOCATION`,
`NO_COMPATIBLE_CREW`, `NO_AVAILABLE_COMPATIBLE_CREW`, `INSUFFICIENT_TIME_CAPACITY`,
`CAPACITY_USED_BY_OTHER_REQUESTS`, `DEFERRED_BY_OPTIMIZER`, ...). `crews[i]` gives each crew's
availability, remaining capacity, locked commitments and the ordered `planned_stops` with estimated
start/finish. `changes_from_previous` lists what changed since the last version. Estimated times
are predictions; a job is completed only when the Crew Portal says so.

Maps show: OSM roads (grey), each crew's colour-coded route following the roads with direction
arrows, numbered stops in planned order, crew position (▲), locked jobs (■ `L` en route / `W` on
site) with dotted locked legs, dashed return-to-depot, backlog (×), pending review (◇) and held (✕)
requests, a legend, the event, time and plan version, and summary statistics.

## 5. Documentation

**Sharing / integrating / testing: [HANDOFF.md](HANDOFF.md).** **Results: [research/report.html](research/report.html)** — self-contained HTML research report (works offline;
figures and maps embedded), regenerated from the saved outputs by `python scripts/build_report.py`.

| Document | Content |
|---|---|
| [research/integration_contract.md](research/integration_contract.md) | schemas, C11 fields, statuses, events, functions, outputs, handoff |
| [research/methodology.md](research/methodology.md) | formulation, objective, constraints, GA, A\*, dynamic policy |
| [research/assumptions.md](research/assumptions.md) | every assumed number with status and sensitivity |
| [research/decision_register.md](research/decision_register.md) | alternatives, decisions, trade-offs |
| [research/data_provenance.md](research/data_provenance.md) | observed vs synthetic vs simulated data, licences |
| [research/experiments.md](research/experiments.md) | splits, pre-registered selection rules, experiment list |
| [research/results.md](research/results.md) | measured results, including negative results |
| [research/limitations.md](research/limitations.md) | what this prototype does not show |

## 6. Folder structure

```
config/default.yaml            all tunable parameters
src/crew_dispatch/             engine.py (stateless optimiser), schemas.py, outputs.py, timepolicy.py, api.py, cli.py
  roadnet/                     network.py, speeds.py (enrichment), router.py (snap, A*, ALT, matrices), prepare.py
  optimize/                    instance.py (objective, insertion), ga.py, greedy.py, exact.py, weights.py, metrics.py
  dynamic/                     controller.py (events, locks, versioning, audit), simulator.py (replay)
  upstream/                    c11_provider.py (+ vendored, unmodified c11_prioritization.py)
  viz/maps.py                  static offline maps
  research/                    data_prep.py, experiments.py, analysis.py, demos.py
tests/                         pytest suite (fixtures need no NYC data)
scripts/verify_upstream.py     upstream integrity check
```
ZIP ≈ 132 MB; extracted ≈ 233 MB (road GraphML 77 MB, enriched network 10 MB, replay data 2 MB,
demo outputs and maps ≈ 135 MB) plus ≈ 300 MB for a virtual environment. No online map tiles are used.
Smoke-tested from the ZIP in a fresh virtual environment: `pip install -r requirements.txt`,
`pytest` (84 passed), `plan`, and `examples/example_propose_plan.py`.
