# CivicOps AI: External Factors (Critical Facility Proximity)

This component checks whether a geolocated NYC311 service request is **adjacent to a critical or sensitive
facility** (hospital, K-12 school, childcare, nursing home / senior care, fire station, police station).
It returns a binary factor with spatial evidence for the Urgency component. Evaluation is
**offline**: it reads a local OpenStreetMap (OSM) cache, with no internet access.

| `external_factor` | Meaning |
|---|---|
| `1` | At least one qualifying facility's mapped geometry lies within **100 m** (straight line, inclusive) of the request |
| `0` | A valid, complete evaluation found no qualifying facility within the threshold |
| `null` | The component could not decide safely (see `incomplete_reason`). Never silently turned into 0 |

**What it does not do:** weather, urgency levels or scores, ranking, request-type urgency rules,
geocoding, agency or skill assignment, crew assignment, routing. It prepares a shared OSM road graph for
the future Crew Dispatch component, but contains no routing logic.

The threshold, data and evidence are documented in `reports/external_factors_report.html` and `DECISIONS.md`.

---

## 1. Installation (tested: Python 3.14.7, Windows 11)

```bash
python -m venv .venv
```
```bash
.venv/Scripts/python -m pip install -r requirements.txt
```
```bash
.venv/Scripts/python -m pip install -e .
```
On macOS/Linux use `.venv/bin/python`. Python ≥ 3.11 is required. The runtime needs only numpy, pandas, pyarrow,
shapely, pyproj, geopandas, pyogrio and PyYAML. OSMnx, matplotlib and scipy are needed for data preparation,
research and maps. *Note:* on the development machine, Windows Application Control blocked a DLL in pyarrow 26.0.0,
so pyarrow is pinned to 23.0.1.

## 2. Folder layout

```
civicops_external_factors/            <- package root (run commands from here)
├── civicops_external_factors/        runtime package (evaluator, rules, spatial engine, CLI)
├── config/
│   ├── proximity_rules.yaml          THE ruleset: thresholds, mode, location policy (edit here, no code changes)
│   ├── facility_categories.yaml      OSM tags per facility category
│   ├── osm_data.yaml                 data-preparation settings (boroughs, buffers, endpoints)
│   └── threshold_selection_protocol.yaml   pre-declared selection criteria
├── data/
│   ├── osm/                          OSM cache (facilities + boundaries = runtime; roads = Crew Dispatch)
│   ├── reference/request_catalog.csv shared request catalog (unchanged copy)
│   └── research/requests_real.parquet historical data (research only; NOT needed at runtime)
├── osm_data_prep/                    download_osm.py, build_manifest.py, validate_osm.py
├── research/                         explore / analyze / sensitivity / select / hold-out scripts
├── visualization/                    static maps (PNG)
├── benchmarks/                       benchmark_proximity.py
├── examples/                         SYNTHETIC example requests + demo.py
├── tests/                            pytest suite (synthetic + real-OSM integration)
├── outputs/                          tables, charts, maps, benchmarks (generated)
├── reports/                          external_factors_report.html (+ build_report.py)
├── README.md  DECISIONS.md  OSM_DATA_README.md  pyproject.toml  requirements.txt  run_demo.py
```

## 3. Quick start (offline)

```bash
python run_demo.py
```
```bash
python -m civicops_external_factors evaluate --input examples/near_facility.json
```
```bash
python -m civicops_external_factors batch --input examples/batch_requests.jsonl --output outputs/batch_results.jsonl
```
```bash
python -m civicops_external_factors validate-cache
```
After `pip install -e .`, the same commands are available as `civicops-ef ...`. Options: `--config`, `--osm-data-dir`,
`--threshold-m 150` (temporary override of every category), `--mode CONTEXT_AWARE`, `--urgency-only` (minimal payload).
Batch input can be a JSON list, JSONL or CSV. Exit codes: 0 ok, 2 invalid input/config, 3 OSM cache problem.

### Python API
```python
from civicops_external_factors import CriticalFacilityEvaluator

evaluator = CriticalFacilityEvaluator.from_config(
    config_path="config/proximity_rules.yaml",
    osm_data_dir="data/osm",          # optional; defaults to the path in the YAML
)
result = evaluator.evaluate(request_context)          # one request (dict)
results = evaluator.evaluate_batch(list_of_requests)  # vectorised batch
result.to_dict()             # full evidence record
result.to_urgency_payload()  # minimal hand-off for Urgency
result.to_json()
```

## 4. Input contract

The input is a **geolocation-enriched** request. The intake bot sends location as text, so a separate Geolocation
Resolution step must add coordinates. This component never geocodes and never invents coordinates.

| Field | Required | Notes |
|---|---|---|
| `request_type` | yes | RT code from the catalog. Validated and echoed. **Not used** by the default rule |
| `latitude`, `longitude` | yes | WGS84 decimal degrees. Numeric strings accepted. Never repaired |
| `geolocation_status` | yes | `RESOLVED`, `APPROXIMATE` are evaluated. `AMBIGUOUS`, `UNRESOLVED`, `FAILED`, `NOT_ATTEMPTED` give null |
| `location_precision` | yes | `ADDRESS_POINT`, `GPS_DEVICE`, `INTERSECTION`, `BLOCKFACE`, `PLACE`, `POSTAL_CODE`, `NEIGHBORHOOD`, `BOROUGH`, `CITY`, `UNKNOWN` |
| `location_uncertainty_m` | optional | Overrides the default uncertainty for the precision class |
| `request_id`, `borough` (or `slots.borough`), `location_source`, `location_type`, `ready_to_submit`, `needs_human`, `emergency_action` | optional | Echoed or checked. They never change the factor (warnings only) |
| `address_type`, `cross_street_2`, `zip_code`, `original_location_text`, `citizen_gps` | optional (future intake fields) | Accepted, never required |

After-the-fact fields (`closed_date`, `resolution_description`, `outcome_class`, `verified_outcome`,
`resolution_hours`, …) are ignored, with the warning `AFTER_THE_FACT_FIELDS_IGNORED`.

**Location uncertainty:** u ≤ 30 m → the point decides (`POINT_ESTIMATE`). 30 < u ≤ 250 m → `ROBUST_INTERVAL`
(1 only if the whole uncertainty disc is inside the threshold, 0 only if it is entirely outside, otherwise null).
u > 250 m, or postal-code/neighbourhood/borough/city precision → null (`INSUFFICIENT_PRECISION`).
Default u per precision: ADDRESS_POINT 15, GPS 20, INTERSECTION 25, BLOCKFACE 120, PLACE 150, UNKNOWN 100 m (heuristics).

## 5. Output contract

| Field | Meaning |
|---|---|
| `external_factor` | 1 / 0 / null: the agreed factor for Urgency |
| `evaluation_status`, `incomplete_reason` | `COMPLETE` or `INCOMPLETE` + one of `MISSING_COORDINATES`, `INVALID_COORDINATES`, `MISSING_GEOLOCATION_STATUS`, `GEOLOCATION_NOT_RESOLVED`, `INSUFFICIENT_PRECISION`, `LOCATION_UNCERTAINTY_SPANS_THRESHOLD`, `OUTSIDE_COVERAGE`, `UNKNOWN_REQUEST_TYPE` (context-aware mode only) |
| `critical_facility_nearby` | Pure geographic evidence (equal to the factor in the default mode) |
| `nearest_facility_overall` | Nearest cached facility of any evidence category (includes exploratory transit stations, `qualifies=false`) |
| `nearest_qualifying_facility`, `nearest_qualifying_distance_m` | Nearest facility of a qualifying category, at any distance |
| `triggering_facilities`, `triggering_facility_count`, `triggering_facilities_truncated` | Qualifying facilities actually within their threshold (sorted by distance, ≤10 listed) |
| `thresholds_m`, `decision_mode` | Applied configuration |
| `triggered_rules` | e.g. `CRITICAL_PROXIMITY_SCHOOL`, `CRITICAL_PROXIMITY_NONE`, `LOCATION_POINT_ESTIMATE`, `INPUT_MISSING_COORDINATES` |
| `explanation` | Plain-English reason (no urgency language) |
| `location_quality` | status, precision, uncertainty (+ source), decision regime, decision margin, boundary sensitivity, borough supplied vs from coordinates |
| `warnings`, `needs_review` | Non-blocking issues. `needs_review` is true for every incomplete evaluation |
| `ruleset_version`, `osm_data_version`, `component_version` | Traceability |

The output never contains urgency levels, weights, scores, ranks or dispatch decisions.

## 6. Configuration (`config/proximity_rules.yaml`)

- `facility_categories.<CATEGORY>.threshold_m`: per-category thresholds (currently 100 m for all six). Set `enabled: false` to drop a category.
- `decision_mode`: `PURE_GEOGRAPHIC` (default) or `CONTEXT_AWARE` (factor forced to 0 for building-interior request types `RT080–RT084`, `RT141`; use only if agreed with Urgency).
- `location.*`: accepted statuses, precision aliases, default uncertainties, regime limits, coverage tolerance.
- `evidence.*`: exploratory categories reported as evidence, max facilities listed, duplicate handling.
Thresholds must be positive finite numbers (validated). The evaluator also checks at load time that the OSM buffer covers
max threshold + max evaluable uncertainty + tolerance.

## 7. Integration with the Urgency component (Khalid)

```python
payload = evaluator.evaluate(req).to_urgency_payload()
# {"request_id", "external_factor", "evaluation_status", "incomplete_reason", "critical_facility_nearby",
#  "triggered_rules", "evidence", "triggering_categories", "explanation", "needs_review",
#  "ruleset_version", "osm_data_version"}
```
- Read `external_factor`. The default mode never uses request type, so there is no double counting with Urgency's request-type rules.
- Treat `null` according to an agreed policy. Shared rule 6 says a failing component must not lower anyone's score.
  If an integer is mandatory, use `to_integer_factor(result, null_policy="ZERO_FLAGGED" | "ONE_FLAGGED")`. It refuses to run without a policy and flags imputed values.
- Field names for this hand-off are still "to be named by Mohammad" in the shared reference. Proposed: `external_factor` (+ the payload above).

## 8. Shared OSM data for Crew Dispatch and Routing

`data/osm/roads/nyc_drive.graphml` (OSMnx `load_graphml`) and `nyc_drive.gpkg` (layers `nodes`, `edges`): a drivable
network for all five boroughs + 1 km, with directionality and edge geometry preserved. See `OSM_DATA_README.md`.
No routing is implemented here. **In this build the road download is still pending** (public Overpass servers were
down; 8 of 18 sub-queries are cached). Complete it with `python osm_data_prep/download_osm.py --only roads`.
External Factors does not need it.

## 9. Reproducing everything

```bash
python osm_data_prep/download_osm.py
```
```bash
python osm_data_prep/build_manifest.py
```
```bash
python osm_data_prep/validate_osm.py --json outputs/tables/osm_validation.json
```
```bash
python research/explore_requests.py
```
```bash
python research/analyze_proximity.py
```
```bash
python research/threshold_sensitivity.py
```
```bash
python research/select_thresholds.py --write-config
```
```bash
python research/holdout_evaluation.py
```
```bash
python benchmarks/benchmark_proximity.py
```
```bash
python visualization/static_facility_map.py
```
```bash
python visualization/static_request_map.py
```
```bash
python -m pytest --junitxml=outputs/tables/test_results.xml
```
```bash
python reports/build_report.py
```
`download_osm.py` skips stages whose outputs exist (`--refresh` forces a re-download, `--only facilities|boundaries|roads`
runs one stage). Delete `outputs/tables/request_distances_*.parquet` to recompute the research distance cache.
Test only the synthetic unit tests (no real cache needed) with `python -m pytest tests/test_proximity_rules.py tests/test_location_validation.py`.

## 10. Troubleshooting

| Symptom | Fix |
|---|---|
| `OSM cache error: ... not found / incomplete` | Unpack the OSM data bundle into `data/osm/` (it must contain `facilities/`, `boundaries/` and `manifest.json`) or pass `--osm-data-dir` |
| `Borough coverage incomplete` | The cache is damaged or partial. Re-run `download_osm.py --refresh --only boundaries` |
| `facility buffer ... smaller than the ruleset needs` | You raised a threshold above the cached buffer. Increase `facility_buffer_m` in `config/osm_data.yaml` and re-download facilities |
| `Input/config error: ... threshold_m` | Thresholds must be positive numbers |
| Overpass 502/500/"at capacity" during download | Public servers are busy. The script retries with backoff across endpoints, and the HTTP cache keeps progress. Re-run later or reorder `overpass.endpoints` |
| `DLL load failed ... Application Control policy` | Windows Smart App Control blocked a wheel. Use the pinned versions in `requirements.txt` |
| Everything is `null` with `MISSING_GEOLOCATION_STATUS` | Supply `geolocation_status` from the Geolocation Resolution step |

Data © OpenStreetMap contributors (ODbL 1.0).
