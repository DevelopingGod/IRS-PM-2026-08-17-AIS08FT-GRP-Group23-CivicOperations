# Data provenance

| Data | Kind | Source | Location in component | Licence / notes |
|---|---|---|---|---|
| NYC311 historical windows | **observed** (sampled) | `IRS-Project-2026-KIMS/data/processed/requests_real.parquet` (authorised, read-only) | `data/replay/requests_windows.parquet` (input-safe columns only), `episodes.parquet` | NYC Open Data (public); used offline for evaluation only |
| Request catalog | project reference | `request_catalog.csv` (authorised) | `data/catalog/request_catalog.csv` (byte copy) | agency / skill vocabulary, `est_service_minutes` (estimates) |
| OSM drive network | **observed** (crowd-sourced) | `civicops_shared_osm_roads` (authorised, read-only), acquired 2026-10-16 via OSMnx 2.1.1 | `data/road_network/source/` (byte copy, SHA-256 verified), `derived/nyc_drive_enriched.npz` | © OpenStreetMap contributors, **ODbL 1.0** — attribution printed on every map |
| Enriched speeds | **imputed** | derived from OSM tags + NYC 25 mph default + class medians | `derived/nyc_drive_enriched.npz` (`edge_speed_source`), `speed_enrichment_report.json` | provenance per edge |
| Original synthetic crews | **synthetic** | `IRS-Project-2026-KIMS/data/processed/synthetic_crews.parquet` | `data/crews/synthetic_crews_original_v1.parquet` (byte copy); `crews_legacy_v1_budget.parquet` | not modified |
| Crews v2 | **synthetic** (generated here) | `research/data_prep.generate_crews_v2`, train split only | `data/crews/crews_v2_cf{0.6,0.8,1.0,1.2}.parquet`, `crews_v2_generation_report.json`, `workforce_sizing_train.csv` | deterministic (no random draws) |
| C11 prioritisation | upstream code | `c11_prioritization.py` (authorised) | `src/crew_dispatch/upstream/vendor/` (unmodified, hash-checked) | used only when the shared-root `c11_final` is absent |
| Simulated events | **simulated** | `dynamic/simulator.py` (seeded) | `outputs/.../events/events.jsonl` (`"simulated": true`) | approvals, Crew Portal updates, durations, breakdowns, review outcomes |
| Optimisation weights | research-defined | `config/default.yaml` + validation selection | `outputs/benchmarks/analysis/tuning_selection.json` | not NYC policy |

Upstream integrity: `scripts/verify_upstream.py record|verify` stores and checks the SHA-256 of every
authorised input (`data/provenance/upstream_hashes.json`).

Replay extract details (`data/replay/replay_build_report.json`): 49,558 rows; 296 episodes
(train 202, val 44, test 50); 3 windows cut at split boundaries; 1,157 rows without coordinates
(reported as `UNROUTABLE_LOCATION`); 2 rows with agency `NYC311-PRD` kept as delivered (no crew →
`NO_COMPATIBLE_CREW`).
