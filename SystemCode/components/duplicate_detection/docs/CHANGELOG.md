# C05 / C06 change log

Owner: Nabeel Taresh. Path: `documents/C05_C06_changelog/CHANGELOG.md`.
Newest entry first. Date format YYYY-MM-DD. Short sentences.

Companion file: `DATA_REGISTER.md` in this folder.

---

## 2026-10-15 — C05 silver labels from NYC 311 duplicate notes (session 4)

### Code added/changed

- `tools/c05_enhance.py`: new `--pull` (public 311 download) and `--silver` (silver labels, kappa, eval).
  No new files. `resolution_description` is used for the label only; the pair table has no resolution column.

### Data added/changed

- Downloaded 203,609 public rows (NYC Open Data 311, erm2-nwe9, Jan-Feb 2026, 9 complaint types) into
  `data/processed/nyc311_open_data/` (git-ignored). 202,860 rows after catalog mapping.
- Silver pairs: 54,964 DUPLICATE + 81,957 NEW (`c05_silver_pairs.csv`, git-ignored).

### Experiments run

- Marker counts: local table 2,778 of 49,636 (5.6%). Open data 60,380 of 202,860 (HEAT/HOT WATER 36%,
  Water System 41%, Rodent 38%, Sewer 15%, Street Condition 10%).
- Kappa silver vs hand labels: 0.05 (n=288, agreement 0.875). Only 8 hand B reports carry a marker.
- February test (60,802 pairs, building-bootstrap CIs): tuned hybrid F1 0.74 [0.73-0.75], false-merge 0.37;
  exact_key 0.74; old hybrid 0.62, false-merge 0.53. January re-tune kept 25 m / 6 h / BBL 48 h.

### Decisions and why

- No code parameter changed. The silver set confirms the tuned hybrid over the old one.
- Street assets (no BBL) favour a radius rule. Per-type radius is an open item.

---

## 2026-10-15 — C05 and C06 enhancement experiments (session 3)

### Code added/changed

- C05: `HYBRID_FUZZY_CUTOFF=0.95` (weak location only). Tuned on synthetic weak-coordinate pairs.
- C05: chronic-site rule added to `hybrid_evidence` (`HYBRID_HOTSPOT_AT`, `HYBRID_GROUP_CAP`). Off. Experiment failed.
- C05: `tools/c05_candidate_set_sheet.py` (labelling sheet for whole candidate sets) and its test.
- C05: `tools/c05_enhance.py` (experiments E1 to E3).
- C06: cold-start rule. A first-time reporter without a burst keeps PROCEED unless p >= 0.60
  (`FIRST_TIME_LOWER_FLOOR`).
- C06: harness fix. `fpr_first_time` is now a true FPR (genuine first-time only). Old value is in
  `first_time_fp_share_old`. New action columns: `fpr_first_time_action`, `recall_urgent_unfounded_action`.
- C06: `tools/c06_experiments.py` (features, models, calibration, CV, per-band thresholds).
- Test: `components/tests/test_c06_with_real_credibility.py` (3 tests). Suite: 28 pass.

### Experiments run (all logged, failures included)

- C05 E1 chronic-site cap: FAILED. A-view DUP F1 0.92 -> 0.16 to 0.49. Wrong-candidate rate stays >= 0.90. Kept off.
- C05 E2 synthetic weak coordinates (885 synthetic rows, tagged): fuzzy 0.95 gives F1 0.64 [0.56-0.72],
  false-merge 0.07. Fuzzy off gives F1 0.00. Missing house number: F1 0.00 for every cutoff.
- C05 E3 building bootstrap: tuned F1 0.92 [0.56-0.98] (pair bootstrap 0.85-0.98). GroupKFold: all 5 folds
  choose 25 m / 6 h / BBL 48 h.
- C06 cold start (test): first-time FPR 0.825 [0.76-0.89] -> 0.325 [0.24-0.41]. Urgent recall 0.713 -> 0.703.
- C06 features: added time/channel/C05 corroboration features gave no AUC gain (about 0.63).
  Burst/dup signals add about 0.02 AUC. Isotonic vs Platt: within 0.003 Brier.
- C06 credibility priors: AUC 0.855, but this is LEAKAGE. Synthetic links were drawn from `hidden_honesty`
  (corr -0.62 with UNFOUNDED). Not shipped.
- C06 per-band urgent threshold: urgent recall >= 0.95 held on val, not always on test (0.85-1.00). It would
  lower priority on more hazards. Not shipped. Team decision.

### Decisions and why

- Ship only changes that pass tests and improve a measured result in the safe direction.
- Test splits were read again. C05 test is now read four times. Prefer the GroupKFold out-of-fold numbers.

### Open items

- Label the candidate sets (sheet is ready).
- Rebuild synthetic reporter links without `hidden_honesty`, so credibility can be measured.
- C06 overall FPR for genuine reports is still 0.83.

---

## 2026-10-15 — Benchmark re-run on hand labels and the full table

### Experiments run

- Ran `tools/eval_c05_c06.py` on Nabeel's Mac with the C05 hand labels and
  `data/processed/requests_real.parquet`.
- C05 (`label_kind=hand`, 295 pairs, one-candidate path): tuned `hybrid_evidence`
  P 0.90, R 0.95, F1 0.92, false-merge 0.10. `exact_key` F1 0.93. Old hybrid
  (`hybrid_legacy`) F1 0.69, false-merge 0.48.
- C06 (`data_origin=processed`, 5,390 test rows, `label_kind=real_verified_outcome_proxy`):
  AUC 0.57 to 0.63. First-time FPR 0.67 to 0.69. Urgent unfounded recall 0.71 to 0.78.
  Correction (session 3): that FPR column used the wrong denominator. True FPR 0.82 to 0.85.

### Data added/changed

- Updated `research/experiments/c05_benchmark.csv`, `research/experiments/c06_benchmark.csv`,
  `research/experiments/c05_c06_benchmark_meta.json` (`c05_provisional=false`).
- Updated `C05-duplicate-results.md` and `C06-fake-results.md`. The old
  provisional (C05 weak) and sample (C06) tables are replaced.

### Decisions and why

- C05 numbers here include tuning pairs. Held-out CIs stay in `C05-tuning-results.md`.

---

## 2026-10-14 — C05 tuning on hand labels, hdbscan fix, BBL fix, multi-candidate eval

### Code added/changed

- `hybrid_evidence` is retuned. A pair links only on a BBL match (48 h open
  window) or on same-type points within 25 m inside 6 h. Fuzzy address is off.
  Graph closure is off. New config: `HYBRID_RADIUS_M=25`, `HYBRID_WINDOW_HOURS=6`,
  `HYBRID_BBL_NEEDS_WINDOW=False`, `HYBRID_FUZZY_CUTOFF=None`, `HYBRID_TRANSITIVE=False`.
- Added `hybrid_legacy` (the old hybrid) for comparison.
- Fixed `hdbscan`: scikit-learn 1.9.1 raises `TypeError` in `epsilon_search`
  with about 40+ points. Fallback is a single-linkage cut at `CLUSTER_EPS_M`.
- Fixed `usable_bbl`: `"2048330028.0"` and `"2048330028"` are now one key.
- Added `tools/eval_c05_multicand.py` (all real candidates per pair).
- Added `components/tests/test_c05_hdbscan_regression.py` (3 tests). Suite: 24 pass.
- Safety rules are unchanged.

### Data added/changed

- Labelled dataset: 300 real pairs from `requests_real.parquet`, seed 2026.
  Labels are AI-made under human supervision (Nabeel). 295 used after UNSURE drop.
- Split: stratified on label, seed 2026, train 177 / validation 59 / test 59.

### Experiments run

- Grid of 480 configs on train/validation. Pooled train+validation re-selection.
- Test (59 pairs, one-candidate path): F1 0.70 -> 0.92, false-merge 0.46 -> 0.00.
  Equal to `exact_key`.
- Multi-candidate eval (mean 42.6 candidates per pair), A view, all 295 pairs:
  tuned hybrid DUPLICATE F1 0.92, false-merge 0.10; old hybrid F1 0.69, false-merge 0.48.
- B view: every strategy merges B with many unlabelled reports at chronic sites.

### Decisions and why

- Real duplicates are at the same point (median 0 m) and mostly share a BBL.
  The old 150 m rule merged neighbour reports.
- The test split was read three times. It is not a clean held-out estimate.

### Open items

- Label whole candidate sets, not only pairs.
- Add a rule for chronic sites (group-size cap or hand-off to C07).
- Collect pairs with weak or missing coordinates to test fuzzy address.
- Full report: `C05-tuning-results.md`.

---

## 2026-10-14 — C05 hand labels on the full real table (AI-made, human-supervised)

### Data added/changed

- Ran a partial pipeline build on Nabeel's Mac: download sample and weather,
  clean, synthetic, NLP, manifest. Aggregates and `write_sample` were skipped
  so no tracked file changed.
- `data/processed/requests_real.parquet`: 49,636 rows, 54 columns, 276
  `sample_window_id` values, 2025-10-19 to 2026-10-11. Not in git.
- New labelling kit in `data/processed/c05_label_kit/` (300 pairs, seed 2026).
- New gold file `data/processed/c05_label_kit/c05_labelled_pairs.csv`.
  300 pairs: 222 NEW, 37 DUPLICATE, 36 RECURRENCE, 5 UNSURE.
  `label_source=hand_ai_supervised`. The loader reads it as `hand`.

### Method

- An AI labeller applied `C05-labelling-rules.md` in six batches of 50.
- Nabeel reviewed every UNSURE, every `[low]`, and 10% of the rest.
- New rules D1-D14 are in `C05-labelling-log.md` with reasons.
- Overlap QC: 50 pairs, kappa 1.000, 100% agreement. Both passes used the same
  rules, so this is rule consistency, not human agreement.

### Experiments run

`tools/eval_c05_c06.py` with output sent to `data/processed/eval_out/`
(the committed `research/experiments/` files were not overwritten). C05 label kind: `hand`.

| C05 strategy | n | Precision DUP | Recall DUP | F1 DUP | False merge | ARI |
|---|---|---|---|---|---|---|
| `exact_key` | 295 | 0.9211 | 0.9459 | 0.9333 | 0.0789 | 0.9206 |
| `hybrid_evidence` (production) | 295 | 0.5211 | 1.0000 | 0.6852 | 0.4789 | 0.4998 |
| `radius_time` | 295 | 0.5211 | 1.0000 | 0.6852 | 0.4789 | 0.4998 |
| `dbscan` | 295 | 0.4868 | 1.0000 | 0.6549 | 0.5132 | 0.4660 |

`fuzzy_address`, `graph`, `hdbscan` and `hybrid_no_fuzzy` equal `radius_time`.

### Decisions and why

- Keep the 300-pair sample. Accept the RT044 skew and log it.
- Record the AI role in `label_source` and notes. The loader accepts any value
  except `weak_heuristic`.

### Open items

- Human blind overlap pass is still needed.
- Production `hybrid_evidence` over-merges on these labels. Check why before the report.
- Re-run the benchmark into `research/experiments/` only after Nabeel agrees.

---

## 2026-10-07 — Move C05/C06 onto the plug-and-play `components/` API

`main` was restructured 5–7 Oct (commits `2007019`..`33e2592`). Components now
live in `components/cxx_<name>/`, share `Reason` + `ComponentResult` in
`components/common.py`, and are verified with `tools/verify_components.py`.
The experiment register is `tools/build_experiment_register.py` →
`research/evidence/00_register/`. Restoring `packages/contracts` and `services/api`
fought that direction, so those trees are removed again.

Field names from the locked v1.0.0 I/O (`DuplicateResult`, `FakeRiskResult`,
`RequestFacts`) are kept as dataclasses. There is no Pydantic package.

Sankalp’s `components/c06_credibility.py` is untouched. Official C06 fake-risk
is `components/c06_fake/`. `handle_message` is not changed.

### Code added/changed

| Path | Purpose |
|---|---|
| `components/c05_duplicate/` | C05 `run()`, strategies, scoring, labelling kit |
| `components/c06_fake/` | C06 `run()`, rules / logreg / GBM / hybrid |
| `components/tests/` | Acceptance + safety + labelling-kit tests (no `civicops_contracts`) |
| `packages/contracts/**` | **Removed** (was restored in the previous entry) |
| `services/api/**` | **Removed** (was restored in the previous entry) |
| `tools/eval_c05_c06.py` | Imports `from components.c05_duplicate` / `c06_fake` |
| `tools/c05_sample_label_pairs.py`, `tools/c05_kappa.py` | Same import retarget |
| `tools/build_experiment_register.py` | C05 and C06 rows added to `REGISTER` |
| `.github/workflows/tests.yml` | `pytest components/tests` (no contracts install) |
| `requirements-ml.txt` | Dropped `-e packages/contracts` |
| `research/notebooks/C05_duplicate.ipynb`, `C06_fake.ipynb` | Paths updated |

### Data added/changed

No new NYC rows. Labelling kit and benchmark CSVs stay where they were.
`research/evidence/00_register/E21t_experiment_register.csv` and README now list C05
and C06 families. E21 chart is regenerated from the register table.

Removed: `packages/contracts/examples/*.json` (fixtures, not data). The same
field names live on the dataclasses.

### Experiments run

Same methods as the v0.1.0 entry. Re-run `py tools/eval_c05_c06.py` after the
move to confirm numbers still match. Safety tests cover every strategy.

### Decisions and why

- Do not restore the shared contracts package. The team’s plug-and-play API
  is dataclasses plus `reasons`. Locked field names are enough for C07/C10/C11.
- Keep C05/C06 as folders (`c05_duplicate/`, `c06_fake/`) because they have
  several strategy modules. Sankalp’s four stay as single files.
- Numbering clash: Sankalp numbered credibility as C06. Official briefing
  C06 is fake-risk. Folder `c06_fake` avoids overwriting `c06_credibility`.
- Old contracts were **not** still needed as a package. Only the field names
  and safety defaults were needed, and those are on the dataclasses.

### Open items

Same as the v0.1.0 entry (hand labels, windows, full parquet, C11 G2).

---

## 2026-10-07 — C05 and C06 v0.1.0: methods, harness, labelling kit

First implementation on branch `cursor/c05-c06-duplicate-fake-28ba`.
`main` no longer had `packages/contracts` or `services/api` (removed 2026-10-05).
C05/C06 still need the locked v1.0.0 models, so the contracts package was restored
**unchanged**. The FastAPI orchestrator was not restored.

### Code added/changed

| Path | Purpose |
|---|---|
| `packages/contracts/**` | Restored locked v1.0.0 models, schemas, examples, contract tests. No schema edits. |
| `services/api/app/components/_base.py` | Shared `DuplicateCandidate`, `meta()`, haversine. Restored helper only. |
| `services/api/app/components/c05_duplicate.py` | Production `run()`. VERSION 0.1.0. Strategy switch. |
| `services/api/app/components/c05/config.py` | 48 h / 30 d / 150 m placeholders and strategy names. |
| `services/api/app/components/c05/geo.py` | Distance, time, address normalisation. |
| `services/api/app/components/c05/scoring.py` | Pair score and `match_evidence` reasons. |
| `services/api/app/components/c05/strategies.py` | exact_key, radius_time, fuzzy, DBSCAN, HDBSCAN, graph, hybrid, ablation. |
| `services/api/app/components/c05/label_kit.py` | Pair sampling, labeller assignment, kappa, drop-in label loader. |
| `services/api/app/components/c06_fake.py` | Production `run()`. VERSION 0.1.0. Never REJECT. |
| `services/api/app/components/c06/config.py` | Bands, burst flag at 5, leakage column list. |
| `services/api/app/components/c06/features.py` | Intake-time features. No closing-note columns. |
| `services/api/app/components/c06/strategies.py` | Rules, calibrated logreg, GBM, hybrid, burst ablation. |
| `services/api/tests/acceptance/test_c05_duplicate.py` | Four locked acceptance tests (restored). |
| `services/api/tests/acceptance/test_c06_fake.py` | Four locked acceptance tests (restored). |
| `services/api/tests/acceptance/test_c05_safety_all_strategies.py` | Safety on every C05 method. |
| `services/api/tests/acceptance/test_c06_safety_all_strategies.py` | Never-reject and corroboration on every C06 method. |
| `services/api/tests/test_c05_label_kit.py` | Sampler schema, no model score, kappa. |
| `services/api/tests/conftest.py` | Skip while VERSION starts with 0.0.0. No FastAPI. |
| `tools/c05_sample_label_pairs.py` | CLI to build the 300-pair kit. Seed 2026. |
| `tools/c05_kappa.py` | Cohen's kappa and disagreement list. |
| `tools/eval_c05_c06.py` | Shared benchmark. Hand labels replace weak labels when present. |
| `.github/workflows/tests.yml` | CI: contracts check + C05/C06 tests. |
| `documents/C05_C06_changelog/*` | This log, data register, labelling rules, report sections. |
| `research/notebooks/C05_duplicate.ipynb` | Experiment notebook for C05. |
| `research/notebooks/C06_fake.ipynb` | Experiment notebook for C06. |

### Data added/changed

| Path | Source | How made | Rows (order of) | Real / synthetic / provisional | Seed |
|---|---|---|---|---|---|
| `packages/contracts/examples/*.json` | Restored from git `aeb02b1` | Export script from Pydantic | 26 examples | Fixture, not NYC rows | n/a |
| `data/sample/c05_label_kit/c05_pairs_to_label.csv` | `requests_real_sample.csv` | Same type + `sample_window_id`, stratified by distance and time | up to 300 unique pairs (sample may be smaller) | Real request ids; labels empty | 2026 |
| `data/sample/c05_label_kit/c05_label_assignments.csv` | Same | Overlap 50 (or fewer on the small sample) + four-way split | one row per (pair, labeller) | Real ids; labels empty | 2026 |
| `data/sample/c05_label_kit/c05_label_sheet_L1.csv` … `L4.csv` | Same | Per-labeller sheets | split of assignments | Real ids; labels empty | 2026 |
| `data/sample/c05_label_kit/c05_weak_eval_pairs.csv` | Same pairs | Distance/time heuristic | same unique pairs, UNSURE dropped at eval | **Provisional weak labels, not hand labels** | 2026 |
| `research/experiments/c05_benchmark.csv` | Weak or hand pairs | `eval_c05_c06.py` | one row per method (+ slices) | Metrics; C05 scores provisional until hand labels | n/a |
| `research/experiments/c06_benchmark.csv` | sample (or processed) `verified_outcome` | `eval_c05_c06.py` | one row per method | Real proxy label; not “proven fake” | n/a |
| `research/experiments/c05_c06_benchmark_meta.json` | harness | kind flags | 1 object | Says which C05 numbers are provisional | n/a |

No new citizen population. Links still come from `synthetic_reporter_links_sample.csv`.
`hidden_honesty` is not a feature. `resolution_description` is not shown to labellers
and is not a C06 feature.

Full `data/processed/requests_real.parquet` (~49,558) is **not** in git. The kit
and models fall back to `data/sample`. Command once processed data exists:

```
py tools/c05_sample_label_pairs.py --source processed --n 300 --out-dir data/processed/c05_label_kit
py tools/eval_c05_c06.py
```

### Experiments run

| Method | Split | Headline | Results file |
|---|---|---|---|
| C05 all strategies + `hybrid_no_fuzzy` | 140 weak-labelled pairs from the sample (seed 2026) | exact_key F1 0.93; other arms F1 1.00 **circular with the weak rule**; hybrid 0.08 ms | `research/experiments/c05_benchmark.csv` |
| C06 rules / logreg / gbm / hybrid / `logreg_no_burst` | sample `split=test`, 163 decided rows | hybrid AUC 0.602 / Brier 0.245; GBM worse; first-time FPR ~0.56 | `research/experiments/c06_benchmark.csv` |

C05 headline numbers are **provisional** (`label_kind=provisional_weak`).
C06 uses real `verified_outcome` (proxy, not “proven fake”).

### Decisions and why

- Restore contracts, not the whole API. Team removed the shared skeleton. C05/C06
  still need the locked signatures. Orchestrator code stays with Sankalp.
- Production C05 = `hybrid_evidence`. Need BBL and radius, plus fuzzy only when
  pins are weak. Hard 500 m cap stops same-street false merges.
- Production C06 = `hybrid_rules_logreg`. Need readable weights and calibration.
  GBM is kept as a comparable arm. Corroboration is a hard monotone adjustment.
- Weak C05 labels are written to a file named `weak` and tagged
  `label_source=weak_heuristic`. They must not be quoted as real results.
- 48 h / 30 d / 150 m stay as documented placeholders. Store 300 m and C07
  180 days are different questions. Not a contract change.
- HDBSCAN uses sklearn (already in `requirements-ml.txt`). If two-point clusters
  fail, the method falls back to radius/time.
- Dropped a separate LightGBM dependency. sklearn `HistGradientBoostingClassifier`
  covers the “gradient boosting” arm without extra install risk in CI.

### Open items

- Hand-label ~300 real pairs. Run kappa on 50 overlap pairs.
- Agree duplicate / recurrence windows with Sankalp (store and C07).
- Retrain C06 on full processed parquet when it is local.
- Confirm C06 operating points with Khalid (C11 G2 and HOLD_FOR_REVIEW).
- Add Nabeel GitHub username to CODEOWNERS when that file returns.
- Challenge `clean.py` outcome rules if they disagree with note analysis.

---

## Template for later entries

```
## YYYY-MM-DD — short title

### Code added/changed
### Data added/changed
### Experiments run
### Decisions and why
### Open items
```

## 2026-10-15 (session 5) - C05 E5 per-type radius and time
- Added HYBRID_TYPE_PARAMS (one dict) to c05 config. hybrid_evidence uses it per request type. Other types keep 25 m / 6 h.
- Added `--per-type` to tools/c05_enhance.py. It tunes on January 2026 silver pairs and tests on February.
- February result: F1 DUPLICATE 0.741 -> 0.763, false merge 0.368 -> 0.276 (building-grouped CIs do not overlap).
- 295 hand pairs: no change (F1 0.921). 28 tests pass. Details: C05-tuning-results.md, section E5.

## 2026-10-15 (session 6) - One file per component, branch nabeel_c05_c06
- Merged components/c05_duplicate/ (7 modules) into components/c05_duplicate.py, and components/c06_fake/ (6 modules) into components/c06_fake.py. Public names and strategy names did not change.
- Each file has a module docstring: inputs, outputs, a usage example, and which components read it (C06, C07, C10, C11; C08 does not).
- Registered both in components/__init__.py as `duplicate` and `fake_risk`, and in health_check(). handle_message() did not change, because C05 needs the store's candidates.
- Moved the labelling kit out of the component into tools/c05_labelling.py (sample, kappa, candidate-sheet).
- Merged 4 scripts into tools/c05_c06_eval.py (benchmark, multicand, c05-exp, c06-exp). Added `benchmark --quick`, which writes to a temp folder.
- Merged 9 test files and helpers into components/tests/test_c05_c06.py. 28 tests pass.
- Merged the C05 results, C05 tuning, C05 labelling log, and C06 results into RESULTS.md. Kept C05-labelling-rules.md.
- Removed .github/workflows/tests.yml. Reason: team decision cff3814 removed automated CI. Run pytest by hand.
- Based on origin/sankalp_code (8c686c1). Register conflict: kept the None-guards and Sankalp's wording, then regenerated research/evidence/00_register/README.md. Updated the C05/C06 register rows to the real numbers. Added 7 C05/C06 rows to research/evidence/all_metrics.csv and a lift block in tools/build_evidence_pack.py.
- Verification: quick benchmark on the real data gives the same C05 numbers as research/experiments/c05_benchmark.csv (hybrid_evidence F1 0.921, exact_key 0.933).
