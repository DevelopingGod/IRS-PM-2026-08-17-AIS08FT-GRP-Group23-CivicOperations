# C05 / C06 data register

Every table or label file these two components use. Update a row when a file
changes. Short sentences. Path is from the repo root.

Licence: NYC 311 Open Data is public. Synthetic tables are team-generated
(seed 2026) and must stay labelled synthetic. Do not commit raw full pulls
(`data/raw/`, `data/processed/` are gitignored).

| Path | Origin | Approx size | Real / synthetic | Sensitivity | Produced by | Used by |
|---|---|---|---|---|---|---|
| `data/sample/requests_real_sample.csv` | NYC 311 sample windows, already in git | ~1,500 rows | Real 311 | Public complaint fields. Has `resolution_description` (label only; C06 must not train on it). | `data.pipeline.build` (committed snapshot) | C05 pairs, C06 train/eval, labelling kit |
| `data/processed/requests_real.parquet` | NYC 311 last 12 months, 5 boroughs. Partial build 2026-10-14: 2025-10-19 to 2026-10-11 | 49,636 rows (2026-10-14 build) | Real 311 | Same. **Not in git.** | `py -m data.pipeline.build` or OneDrive `05_Data` unzip | Same, when present |
| `data/sample/synthetic_reporter_links_sample.csv` | Shared synthetic population | ~1,500 rows | Synthetic links on real request ids | No real PII. `citizen_id` is fake. | `data.pipeline.synthetic` | C06 burst / reporter history |
| `data/processed/synthetic_reporter_links.parquet` | Same, full | ~49,558 rows | Synthetic | Same. **Not in git.** | `data.pipeline.synthetic` | C06 when processed exists |
| `data/sample/synthetic_citizens.csv` | Shared synthetic population | 5,000 rows | Synthetic | `hidden_honesty` is **eval only**. C06 must not read it as a feature. | `data.pipeline.synthetic` (seed 2026) | C06/C07 population; not a C06 feature |
| `data/processed/synthetic_citizens.parquet` | Same | 5,000 rows | Synthetic | Same. **Not in git.** | `data.pipeline.synthetic` | Same |
| `data/processed/synthetic_burst_requests.parquet` | Burst archetype extra rows | ~1,391 rows | Synthetic | Fake requests. **Not in git.** No sample copy committed. | `data.pipeline.synthetic.make_bursts` | C06 burst (when present). Harness reconstructs 1 h counts from links if missing. |
| `data/reference/outcome_base_rates.csv` | Aggregated from real decided rows | 34 types | Real rates | Public | Data pipeline / notebook 01 | C06 type base rate |
| `data/reference/request_catalog.csv` | Team catalog | 34 types | Reference | Public | Team | C05/C06 type and urgency |
| `data/sample/c05_label_kit/c05_pairs_to_label.csv` | Built from requests sample | 146 unique pairs (header + 146) | Real ids, empty labels | No closing notes. No model score. | `py tools/c05_sample_label_pairs.py` seed 2026 | Labellers |
| `data/sample/c05_label_kit/c05_label_assignments.csv` | Same | 196 rows (50 overlap pairs ×2 + 96 solo) | Real ids | Labeller ids L1–L4 only | Same script | Overlap plan |
| `data/sample/c05_label_kit/c05_label_sheet_L*.csv` | Same | L1/L2 74 rows, L3/L4 24 rows | Real ids, empty `label` | Fill DUPLICATE/NEW/RECURRENCE/UNSURE | Same script | Four labellers |
| `data/sample/c05_label_kit/c05_weak_eval_pairs.csv` | Same pairs + heuristic | 146 | **Provisional weak labels** | Not a human gold set | `attach_weak_labels` | C05 harness until hand labels exist |
| `data/sample/c05_label_kit/README.md` | Kit notes | n/a | n/a | n/a | Written with the kit | Labellers |
| `data/sample/c05_label_kit/c05_labelled_pairs.csv` | Team hand labels (drop-in) | ~300 when done | Real pairs, hand labels | Agreed gold. No model score. | Labellers + kappa adjudication | C05 harness (**replaces** weak file) |
| `data/processed/c05_label_kit/*` | Full-data kit | ~300 | Real | **Not in git** (processed/). Copy gold CSV into `data/sample/c05_label_kit/` or set `C05_LABELS_CSV` | `--source processed` | Labelling on full table |
| `data/processed/c05_label_kit/c05_labelled_pairs.csv` | 300 pairs from the full table, seed 2026 | 300 rows (222 NEW, 37 DUPLICATE, 36 RECURRENCE, 5 UNSURE) | Real pairs, hand labels | **AI-made under Nabeel's supervision.** `label_source=hand_ai_supervised`. **Not in git.** | AI labeller + Nabeel review, rules D1-D14 in `C05-labelling-log.md` | C05 harness (read first, as `hand`) |
| `data/processed/c05_label_kit/work/*` | Batch files, review files, overlap passes, decisions | 6 batches + 6 reviews + 3 QC files | Real ids | Scratch. **Not in git.** | Labelling session 2026-10-14 | Audit trail |
| `data/processed/eval_out/*` | Benchmark run on the hand labels | 3 files | Metrics | None. **Not in git.** | `tools/eval_c05_c06.py` (OUT redirected) | Report draft |
| `research/experiments/c05_benchmark.csv` | Harness output | few dozen rows | Metrics | None | `py tools/eval_c05_c06.py` | Report table |
| `research/experiments/c06_benchmark.csv` | Harness output | few rows | Metrics | None | Same | Report table |
| `research/experiments/c05_c06_benchmark_meta.json` | Harness | 1 object | Flags provisional vs hand | None | Same | Report caveat |
| `research/evidence/00_register/E21t_experiment_register.csv` | Team register | one row per algorithm family | Metrics notes | None | `tools/build_experiment_register.py` | Examiner page; now includes C05/C06 |

## 2026-10-15 — NYC Open Data 311 pull and C05 silver labels

- Origin: NYC Open Data, 311 Service Requests from 2010 to Present, dataset id `erm2-nwe9`,
  Socrata API `https://data.cityofnewyork.us/resource/erm2-nwe9.csv`, public, no key. Pulled 2026-10-15.
- Filter: `created_date` 2026-01-01 to 2026-02-28; complaint types HEAT/HOT WATER, Street Condition,
  Water System, Rodent, PLUMBING, Sewer, Dirty Condition, Illegal Dumping, Dead Animal. 15 fields
  (unique_key, dates, type, descriptor, address, address_type, bbl, lat/lon, status,
  resolution_description, channel, borough, agency).
- Files (git-ignored): `data/processed/nyc311_open_data/raw_2026-01_*.csv`, `raw_2026-02_*.csv`
  (203,609 rows, about 108 MB). Reproduce: `python tools/c05_enhance.py --pull`.
- Derived (git-ignored): `data/processed/nyc311_open_data/c05_silver_pairs.csv`. 136,921 pairs
  (54,964 DUPLICATE, 81,957 NEW). `label_source=silver_nyc311_resolution`. Real reports, labels from city notes.
- Leakage: `resolution_description` is label-only. It is not in the pair table and C05 never reads it.
- No data left the machine. DigitalOcean Spaces was not used.

## 2026-10-15 — C05 and C06 enhancement experiments

- New (git-ignored): `data/processed/c05_label_kit/candidate_sets/c05_candidate_set_sheet.csv`.
  295 sets, 7,020 rows, real ids, labels blank. Built by `tools/c05_candidate_set_sheet.py`.
- New synthetic (local, not tracked): `/tmp/irs-tune/out/c05_e2_synthetic_pairs.csv`. 885 rows made from
  the 295 hand-labelled pairs. `is_synthetic=True`, `label_source=synthetic_from_<source>`. BBL removed;
  B coordinates dropped or jittered 30-120 m; or house number removed. Gold label kept. Seed 2026.
- C06 data: processed table, decided rows only, temporal split train 24,116 / val 5,787 / test 5,390.
- Known issue: `synthetic_reporter_links.parquet` was generated from `hidden_honesty`. Reporter-history
  features from it carry the label. Do not use them to measure C06.
- Outputs (local, not tracked): `/tmp/irs-tune/out/c05_*`, `/tmp/irs-tune/out/c06_*`.
- No data left the machine. DigitalOcean Spaces was not used.

## 2026-10-14 — C05 tuning and multi-candidate evaluation

- Labelled dataset: `data/processed/c05_label_kit/c05_labelled_pairs.csv`.
  300 real report pairs, seed 2026. Labels are AI-made under Nabeel's
  supervision. 295 used (UNSURE dropped). Not in git.
- Context data: `data/processed/requests_real.parquet` (49,636 rows). Same type,
  48 h before B, within 500 m; OPEN/closed as of B's time. Mean 42.6 candidates.
- BBL is float text in the label CSV and integer text in the parquet.
  `usable_bbl` now treats both as one key.
- Split: stratified on label, seed 2026 (177 / 59 / 59). Test read three times.
- Outputs (local, not tracked): `/tmp/irs-tune/out/` (grid, test and
  multi-candidate results).
- No data left the machine. DigitalOcean Spaces was not used.
- Report: `C05-tuning-results.md`.

## Drop-in rule for C05 gold

1. Finish sheets. Run `py tools/c05_kappa.py`.
2. Write agreed rows to `c05_labelled_pairs.csv` with `label_source=hand`.
3. Put the file at `data/sample/c05_label_kit/c05_labelled_pairs.csv` or set
   `C05_LABELS_CSV`.
4. Re-run `py tools/eval_c05_c06.py`. The weak file is ignored when the hand
   file has DUPLICATE/NEW/RECURRENCE rows.

## Columns C06 must never use as features

`resolution_description`, `outcome_class`, `verified_outcome` (label only),
`closed_date`, `resolution_hours`, `status`, `hidden_honesty`.

## Label disclosure

The C05 gold labels from 2026-10-14 are AI-made under human supervision.
Nabeel Taresh reviewed every batch and approved every rule. Overlap QC used the
same rules twice, so it shows rule consistency, not human agreement. See
`C05-labelling-log.md`.

## 2026-10-15 (session 5) - C05 E5 per-type tuning
- Input: data/processed/nyc311_open_data/c05_silver_pairs.csv (E4, git-ignored). No new download.
- Train: 2026-01 pairs. HEAT/HOT WATER tuned on an 8,000-pair label-stratified sample, seed 2026. Test: 2026-02 pairs.
- resolution_description is used only for labels, never as a feature.
- Outputs (not tracked): /tmp/irs-tune/out/c05_e5_per_type_choice.csv, c05_e5_per_type_results.csv.

## 2026-10-15 (session 6) - No new data
- The consolidation did not add data. data/processed/ (requests, hand labels, NYC Open Data pull) stays git-ignored and is not in the branch.
- Committed small files only: data/sample/c05_label_kit/ (sample kit), research/experiments/c05_benchmark.csv, c06_benchmark.csv, c05_c06_benchmark_meta.json, data/reference/nyc311_agencies.csv.
