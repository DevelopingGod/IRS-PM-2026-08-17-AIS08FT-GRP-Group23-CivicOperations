# C05 / C06 results

Owner: Nabeel Taresh. One file for the report. It joins the old results, tuning, and labelling
documents without change to their text, except the paths below.

| Part | What it is |
|---|---|
| 1 | C05 method, contract, current benchmark (295 hand pairs) |
| 2 | C05 tuning: grid, multi-candidate evaluation, E1-E5 experiments, silver labels |
| 3 | C05 labelling log (AI labels under human supervision, seed 2026) |
| 4 | C06 method, current benchmark, experiments, cold-start rule |

Code: `components/c05_duplicate.py`, `components/c06_fake.py` (one file each).
Tests: `components/tests/test_c05_c06.py`.
Scripts: `tools/c05_c06_eval.py` (benchmark, multicand, c05-exp, c06-exp) and
`tools/c05_labelling.py` (sample, kappa, candidate-sheet).
Labelling rules: `C05-labelling-rules.md`. Dated log: `CHANGELOG.md`. Data: `DATA_REGISTER.md`.


---

# Part 1. C05 duplicate detection: method and current results

Owner: Nabeel Taresh. Locked field names from contract v1.0.0, now dataclasses
on the plug-and-play `components/` API. Component version 0.1.0.
Path: `components/c05_duplicate.py`. `from components.c05_duplicate import run`.

Write this section into the final report. Short sentences. Plain words.

### Purpose

C05 groups reports of the same physical incident. One crew goes once. Extra
reporters become corroboration for C06, C10 and C11. After a closure, the same
place and type is a recurrence for C07. It is not a duplicate.

### Inputs and outputs (contract)

Input: `RequestFacts` plus `DuplicateCandidate` list from the store (same type,
about 300 m). Output: `DuplicateResult` (`NEW` / `DUPLICATE` / `RECURRENCE`,
group id, primary flag, corroboration, channels, `match_evidence`).

SAFE_DEFAULT on failure: `NEW`, `is_primary=True`. It never merges on error.

### How it works

Every method uses the same safety filters. Different types never merge. Only an
OPEN candidate can absorb a duplicate. A closed same-place hit is RECURRENCE.
Coordinates farther than 500 m never match.

| Method | What it does | Role |
|---|---|---|
| `exact_key` | Same BBL and type inside the time window | Precise when BBL is present |
| `radius_time` | Distance plus time | Baseline every other method must beat |
| `fuzzy_address` | Street-text similarity when pins are weak | Helps BLOCKFACE / missing geocode |
| `dbscan` | Density clusters in metres + scaled hours | Mixed-density areas |
| `hdbscan` | Variable-density clusters | Dense cores with sparse tails |
| `graph` | NetworkX connected components on pairwise edges | Multi-channel cascades |
| `hybrid_evidence` | BBL + radius/time + fuzzy, then the graph | **Production** |
| `hybrid_no_fuzzy` | Same hybrid without fuzzy address | Ablation |

Production `run()` uses `hybrid_evidence`. Switch with `strategy=` or `C05_STRATEGY`.

Windows used (placeholders — team still needs to agree):

- Open duplicate: 48 hours
- Recurrence: 30 days
- ADDRESS radius: 150 m in the scorer. The tuned `hybrid_evidence` links only
  on the same BBL (48 h) or same-type points within 25 m inside 6 h (2026-10-14).
- INTERSECTION / BLOCKFACE radius: 75 m, and extra text or BBL evidence
- Store gather radius (not owned here): 300 m
- C07 chronic recurrence (Sankalp): 150 m / 180 days

These numbers are not the same on purpose. 48 h asks “is this the same incident
still open?”. 180 days asks “does this place keep breaking?”. See Open items.

### Why the hybrid is the production method

`exact_key` misses rows with no BBL (the acceptance fixture has none).
`radius_time` is clear and safe. Fuzzy text helps weak pins, but far points that
share a street name must not merge. The hybrid keeps the hard distance cap, then
uses NetworkX so a chain of close pairs becomes one group.

We pick by **false-merge rate first**, then F1 and ARI. A false merge sends a
crew to the wrong job.

### Evaluation

Shared harness: `py tools/c05_c06_eval.py benchmark`. Time-based `split`. Results:
`research/experiments/c05_benchmark.csv`.

**These C05 numbers are real hand-label results** (`label_kind=hand`, 2026-10-15).
The labels are 295 real report pairs from `requests_real.parquet` (seed 2026,
UNSURE dropped). The labels are AI-made under Nabeel's supervision. The harness
gives one candidate per pair. The numbers cover all 295 pairs, so they include
the pairs used for tuning. For held-out test CIs, see `RESULTS.md`.

Headline (all 295 hand-labelled pairs, one-candidate path, run 2026-10-15):

| Method | n | Prec | Rec | F1 | False merge | ARI | ms |
|---|---|---|---|---|---|---|---|
| exact_key | 295 | 0.92 | 0.95 | 0.93 | 0.08 | 0.92 | 0.01 |
| radius_time | 295 | 0.52 | 1.00 | 0.69 | 0.48 | 0.50 | 0.03 |
| fuzzy_address | 295 | 0.52 | 1.00 | 0.69 | 0.48 | 0.50 | 0.02 |
| dbscan | 295 | 0.49 | 1.00 | 0.65 | 0.51 | 0.47 | 0.67 |
| hdbscan | 295 | 0.52 | 1.00 | 0.69 | 0.48 | 0.50 | 0.41 |
| graph | 295 | 0.52 | 1.00 | 0.69 | 0.48 | 0.50 | 0.30 |
| hybrid_legacy (old production) | 295 | 0.52 | 1.00 | 0.69 | 0.48 | 0.50 | 0.03 |
| hybrid_evidence (tuned, production) | 295 | 0.90 | 0.95 | 0.92 | 0.10 | 0.91 | 0.02 |
| hybrid_no_fuzzy (ablation) | 295 | 0.90 | 0.95 | 0.92 | 0.10 | 0.91 | 0.02 |

The old provisional table (140 weak pairs, 2026-10-07) gave F1 1.00 for most
methods. That result was circular, because the weak rule was the same as
`radius_time`. Do not quote it.

Fuzzy address (weak location only) now uses cutoff 0.95. On synthetic weak-coordinate
pairs it gives F1 0.64 [0.56-0.72] and false-merge 0.07 (2026-10-15). With building
bootstrap CIs, tuned F1 is 0.92 [0.56-0.98].

Silver check (2026-10-15): on 60,802 February 2026 pairs labelled from the city's
own duplicate notes, tuned hybrid F1 0.74 [0.73-0.75] and `exact_key` 0.74; old
hybrid 0.62. Kappa against the hand labels is 0.05 (low coverage: NYPD and snow
reports carry no marker). See `RESULTS.md` section 13.

Full slices: `research/experiments/c05_benchmark.csv`. Multi-candidate results and
lessons: `RESULTS.md`.

### Explainability

`match_evidence` holds `Reason` rows: `BBL_KEY_MATCH`, `DISTANCE_WITHIN_RADIUS`,
`FUZZY_ADDRESS_STRONG`, `WITHIN_OPEN_WINDOW`, and so on. Codes are UPPER_SNAKE.
Messages are one plain sentence. The admin drill-down shows them as written.

### Labelling kit

See `C05-labelling-rules.md` in this folder. Scripts:
`py tools/c05_labelling.py sample` and `py tools/c05_labelling.py kappa`.
Seed 2026. About 50 pairs are double-coded. Cohen's kappa on the overlap.
The rest split across four people.

### Business

Fewer wasted second trips. A shorter working queue. Every reporter in the group
can be told when the primary is closed. Track operator override rate when the
admin unmerges a group.

### Limitations

- The 300-pair gold set is in `data/processed/` (not in git). It is AI-made under
  human supervision.
- Weak labels treat “close in space and time” as duplicate. That overstates
  easy cases and is not a human judgement.
- INTERSECTION pins are noisy.
- Windows are still placeholders.
- DBSCAN / HDBSCAN on two points at intake often fall back to radius/time.

### Technique group mapping

Knowledge Discovery (clusters and graph groups), Decision Automation (open vs
recurrence rules), Cognitive Systems (explainable evidence lines).

### Team decisions needed

1. Keep 48 h / 30 d / 150 m, or change them?
2. Should the store 300 m gather stay larger than the C05 150 m match?
3. Who helps label the 300 pairs, and by which date?
4. Add Nabeel’s GitHub handle to CODEOWNERS (file was removed with the old API).


---

# Part 2. C05 tuning and experiments (E1-E5)

Date: 2026-10-14. Owner: Nabeel. Status: proposal. The patch is not applied to the real clone.

### 1. Scope

This work tunes the C05 production strategy `hybrid_evidence`.
It uses the 300 hand-labelled pairs in `data/processed/c05_label_kit/c05_labelled_pairs.csv`.
The loader drops UNSURE and unresolved overlap rows. 295 pairs remain: NEW 222, DUPLICATE 37, RECURRENCE 36.
All work ran in a scratch copy at `/tmp/irs-tune/repo`. Nothing was committed, pushed, or uploaded.

### 2. Diagnosis

#### 2.1 Why the old hybrid over-merges

- The old hybrid made 71 DUPLICATE calls on all 295 pairs. 34 were false merges. All 34 have gold label NEW.
- 31 of the 34 false merges had no BBL match. The radius/time rule alone made them.
- 3 of the 34 false merges had a BBL match.
- 0 false merges came from fuzzy address. All pairs have usable coordinates, so the fuzzy rule never fires.
- 0 false merges came from graph transitivity. The evaluation gives each pair only one candidate, so a graph cannot chain.
- The score rule is `0.40 + 0.45 * (1 - d/150)`. It passes the 0.45 floor for any ADDRESS point within about 135 m. Time inside 48 h adds no score. So the rule is "same type, open, within 135 m, within 48 h".
- True duplicates are almost always at the same point: median distance 0 m, maximum 21 m. 35 of 37 share a BBL.
- False merges are spread from 0 m to 133 m (median 40 m). 23 of 34 are RT044 snow pairs. Neighbour reports on one street are separate incidents.
- The old hybrid also called 29 gold-NEW pairs RECURRENCE. The same loose place rule causes this.

#### 2.2 Why many strategies score the same

- `tools/c05_c06_eval.py benchmark` calls `run(facts, [candidate])` with one candidate per pair.
- With one candidate, `graph` has one edge. `hybrid_evidence` returns that edge. `hdbscan` and `dbscan` have two points and fall back to `radius_time` when they find no cluster.
- `fuzzy_address` adds no score when coordinates are reliable.
- So `radius_time`, `fuzzy_address`, `hdbscan`, `graph`, and `hybrid_evidence` give identical predictions on 100% of pairs. `dbscan` agrees on 96%.
- This is a shared evaluation path, plus one shared scorer (`pair_match`). It is not a bug in one strategy.
- New finding: with real context candidates (mean 42 per pair from `requests_real.parquet`), `hdbscan` raises a `TypeError` inside scikit-learn (`cluster_selection_epsilon` path) on 44 of 59 test pairs. The one-candidate evaluation hid this.

### 3. Method

1. Split the 295 pairs with a stratified split on label, seed 2026: train 177, validation 59, test 59 (7 DUPLICATE in test).
2. Add context: for each pair, add every same-type request from `requests_real.parquet` that came in the 48 h before report B and lies within 500 m. A candidate is OPEN if it was not closed before B. This lets graph transitivity act.
3. Score a pair as DUPLICATE when A is linked and OPEN and inside the window. Score RECURRENCE when A is linked and closed inside 30 days. Else NEW.
4. Optimise F1 for DUPLICATE. Keep the false-merge rate (false DUPLICATE calls / all DUPLICATE calls) at or below 0.15.
5. Compute test metrics with a bootstrap of 1,000 resamples of test pairs (seed 2026). Report 2.5% and 97.5% percentiles.

Safety rules stay in the code: different types never merge; only OPEN candidates become DUPLICATE; a closed candidate can only give RECURRENCE; points beyond 500 m never merge.

### 4. Grid

- Radius for non-BBL links: 0 (same point only), 25, 50, 75, 100, 150 m.
- Time window for radius and fuzzy links: 6, 12, 24, 48 h.
- Fuzzy cutoff (weak location only): off, 0.72, 0.80, 0.88, 0.95.
- Graph transitivity: on, off.
- BBL rule: BBL alone (48 h open window) or BBL plus the tuned time window.
- 480 configurations. Full results: `/tmp/irs-tune/out/grid.csv`.

Observations:
- All labelled pairs are less than 11 h apart. So 12, 24, and 48 h give the same result. Only 6 h differs.
- The fuzzy cutoff changed no result. Fuzzy only acts when coordinates are missing or weak, and no labelled pair has that.
- Graph transitivity changed one train pair at most.
- 10 of 37 true duplicates are more than 6 h apart. All of them share a BBL. A 6 h window is safe only for non-BBL links.

### 5. Selection (two rounds, stated in full)

Round 1. Rule: best validation F1 with train and validation false-merge rate at or below 0.15. Result: radius 0 m, window 6 h for all links, fuzzy off, transitivity on. Validation F1 0.80. This config failed the acceptance test `test_same_incident_other_channel_is_linked` (two no-BBL points 11 m apart must merge). I looked at the test split once for this config: F1 0.73 [0.29-1.00], recall 0.57. It applied the 6 h window to BBL links and lost true duplicates.

Round 2. The validation split has only 8 duplicates, so it is noisy. I pooled train and validation (236 pairs, 30 duplicates). I kept only radii of 25 m or more, so that the acceptance test can pass. Rule: best pooled F1 with pooled false-merge rate at or below 0.15. Ties go to the simpler config (transitivity off, fuzzy off).

Chosen parameters (`components/c05_duplicate.py`):

| Parameter | Old | New |
|---|---|---|
| Link rule | score >= 0.45 (about 135 m) | BBL, or same-type points within `HYBRID_RADIUS_M` |
| `HYBRID_RADIUS_M` | (150 m scorer) | 25 m |
| `HYBRID_WINDOW_HOURS` (non-BBL links) | 48 h | 6 h |
| `HYBRID_BBL_NEEDS_WINDOW` | n/a | False (BBL keeps 48 h) |
| `HYBRID_FUZZY_CUTOFF` | 0.88 / 0.72 | off (None) |
| `HYBRID_TRANSITIVE` | on (graph group) | off |
| `HARD_MAX_DISTANCE_M` | 500 m | 500 m (unchanged) |

Pooled train+validation: P 0.88, R 0.97, F1 0.92, false-merge 0.12.

Important: the test split was read twice (round 1 and round 2). The round 2 test numbers are therefore not a fully clean held-out estimate. A fresh labelled set is needed to confirm them.

### 6. Test results (59 pairs, 7 DUPLICATE; one-candidate evaluation path)

| Strategy | n (dup) | Precision [95% CI] | Recall [95% CI] | F1 [95% CI] | False-merge [95% CI] |
|---|---|---|---|---|---|
| exact_key | 59 (7) | 1.00 [1.00-1.00] | 0.86 [0.50-1.00] | 0.92 [0.67-1.00] | 0.00 [0.00-0.00] |
| hybrid_evidence (old) | 59 (7) | 0.54 [0.27-0.82] | 1.00 [1.00-1.00] | 0.70 [0.42-0.90] | 0.46 [0.18-0.73] |
| hybrid_evidence (tuned) | 59 (7) | 1.00 [1.00-1.00] | 0.86 [0.50-1.00] | 0.92 [0.67-1.00] | 0.00 [0.00-0.00] |
| radius_time | 59 (7) | 0.54 [0.25-0.80] | 1.00 [1.00-1.00] | 0.70 [0.40-0.89] | 0.46 [0.20-0.75] |
| fuzzy_address | 59 (7) | 0.54 [0.27-0.82] | 1.00 [1.00-1.00] | 0.70 [0.42-0.90] | 0.46 [0.18-0.73] |
| dbscan | 59 (7) | 0.50 [0.23-0.79] | 1.00 [1.00-1.00] | 0.67 [0.38-0.88] | 0.50 [0.21-0.77] |
| hdbscan | 59 (7) | 0.54 [0.27-0.80] | 1.00 [1.00-1.00] | 0.70 [0.43-0.89] | 0.46 [0.20-0.73] |
| graph | 59 (7) | 0.54 [0.25-0.85] | 1.00 [1.00-1.00] | 0.70 [0.40-0.92] | 0.46 [0.15-0.75] |

With context candidates, the numbers are the same for every strategy except `dbscan` (F1 0.64) and `hdbscan` (F1 0.36, because of the crash). See `/tmp/irs-tune/out/test_results.csv`.

#### 6.1 Test results without RT044 snow pairs (16 pairs, 5 DUPLICATE)

| Strategy | n (dup) | Precision [95% CI] | Recall [95% CI] | F1 [95% CI] | False-merge [95% CI] |
|---|---|---|---|---|---|
| exact_key | 16 (5) | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 0.00 [0.00-0.00] |
| hybrid_evidence (old) | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |
| hybrid_evidence (tuned) | 16 (5) | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 0.00 [0.00-0.00] |
| radius_time | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |
| fuzzy_address | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |
| dbscan | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |
| hdbscan | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |
| graph | 16 (5) | 0.83 [0.50-1.00] | 1.00 [1.00-1.00] | 0.91 [0.67-1.00] | 0.17 [0.00-0.50] |

#### 6.2 Reading the table

- The tuned hybrid removes every false merge on test. Its DUPLICATE metrics equal `exact_key`.
- The tuned hybrid does not beat `exact_key`. The CIs are wide because the test split has 7 duplicates.
- Three-class accuracy is 0.92 for the tuned hybrid and 0.98 for `exact_key`. The 25 m rule still makes some extra RECURRENCE calls.
- Without RT044 the old hybrid looks good (F1 0.91). The snow pairs hold most of the false merges.

### 7. Lessons

- Weak labels hid the problem. The weak heuristic labels used the same distance and time logic as the scorer. So the scorer agreed with its own rule and looked accurate.
- A one-candidate evaluation cannot test clustering or graph grouping. Five strategies looked different in code but were the same in the evaluation.
- Real duplicates in this data are at the same address. Distance is weak evidence. A shared BBL is strong evidence.
- Recall of 1.0 is a warning sign. It meant the rule accepted every near pair.
- A small validation split (8 duplicates) can choose a bad config. Pooling and acceptance tests caught it.

### 8. Limits

- Only 37 duplicates in total. Test CIs are wide (F1 0.67 to 1.00).
- RT044 snow is 64% of the pairs. One building gives 41 pairs. The split is stratified by label only, not by building, so pairs from one building can be in train and test.
- No pair has missing or weak coordinates. The fuzzy and unreliable-address rules are not tested by these labels.
- The test split was used twice (see section 5).
- Labels are "AI-made under human supervision" for part of the set (`label_source`).
- Graph transitivity is kept in code behind a flag but is not validated.
- `hdbscan` fails with more than a few candidates. This needs a fix or a guard before any use.

### 9. Files

- Patch: `/tmp/irs-tune/c05_tuning.patch` (session 2: changes `config.py`, `geo.py`, `strategies.py`; adds `hybrid_legacy`, `components/tests/test_c05_hdbscan_regression.py`, `tools/c05_c06_eval.py multicand`).
- Scripts: `/tmp/irs-tune/diag.py`, `c05_tune.py`, `c05_reselect.py`, `c05_test.py`.
- Outputs: `/tmp/irs-tune/out/` (diag.csv, grid.csv, grid_pooled_acceptance_safe.csv, test_results.csv, test_results_v1.csv).
- Tests: 21 of 21 pass on the tuned code (session 1). 24 of 24 pass after session 2.

### 10. Draft documentation entries

#### CHANGELOG.md (draft)

```
### 2026-10-14 - C05 hybrid_evidence tuned on hand labels (proposal, not merged)
- Changed: hybrid_evidence now links a pair only on a BBL match (48 h open window)
  or on same-type points within 25 m inside 6 h. Fuzzy address is off. Graph closure is off.
- Added: hybrid_legacy strategy (the old hybrid) for comparison.
- Added config: HYBRID_RADIUS_M=25, HYBRID_WINDOW_HOURS=6, HYBRID_BBL_NEEDS_WINDOW=False,
  HYBRID_FUZZY_CUTOFF=None, HYBRID_TRANSITIVE=False.
- Result (hand-label test, 59 pairs): F1 0.70 -> 0.92, false-merge 0.46 -> 0.00.
  Equal to exact_key. CIs are wide. Test split was read twice.
- Found: the eval gives one candidate per pair, so graph/cluster strategies cannot differ.
- Found: hdbscan raises TypeError with real context candidates.
- Safety rules unchanged. 21/21 tests pass.
```

#### DATA_REGISTER.md (draft)

```
### 2026-10-14 - C05 tuning inputs and outputs
- Input: data/processed/c05_label_kit/c05_labelled_pairs.csv (300 pairs; 295 after UNSURE/overlap drop).
  Git-ignored. Hand labels, part AI-made under human supervision.
- Input: data/processed/requests_real.parquet (49,636 rows). Git-ignored. Used for context candidates
  (same type, 48 h before, within 500 m).
- Split: stratified on label, seed 2026, train 177 / validation 59 / test 59.
- Output (local, not tracked): /tmp/irs-tune/out/grid.csv, grid_pooled_acceptance_safe.csv,
  test_results.csv, diag.csv.
- Known skew: RT044 snow is 64% of pairs; one building gives 41 pairs.
- No data left the machine. DigitalOcean Spaces was not used.
```


### 11. Multi-candidate evaluation (added 2026-10-14, second session)

#### 11.1 Code changes in this session

- Fix: `hdbscan` crashed. scikit-learn 1.9.1 raises `TypeError` in `epsilon_search` (`sklearn/cluster/_hdbscan/_tree.pyx`) when `cluster_selection_epsilon` is set and there are about 40 or more points. The fix catches `TypeError` and `ValueError` and then uses a single-linkage cut at `CLUSTER_EPS_M`. With `min_samples=1` this cut gives the same groups as the epsilon rule. Groups of one become noise.
- Fix: BBL text did not match across sources. The label CSV stores `2048330028.0`. The parquet stores `2048330028`. `usable_bbl` now removes a trailing `.0`. Before this fix, a BBL in the label CSV never matched a BBL from the parquet.
- New tests: `components/tests/test_c05_hdbscan_regression.py` (3 tests). The 40-candidate test fails on the old code with the same `TypeError`. It passes on the new code.
- New script: `tools/c05_c06_eval.py multicand`.

#### 11.2 Method

1. Use all 295 non-UNSURE pairs. Use the same stratified split as before (seed 2026).
2. For each pair, take report B. Give B every real request from `requests_real.parquet` with the same type, created in the 48 h before B, within 500 m. Add the labelled A. Mean candidates per pair: 42.6 (min 1, median 18, max 372).
3. Set OPEN or closed as of B's time. A candidate is OPEN if it has no close date or it closed after B.
4. Run each strategy once on the full candidate set. Then apply `pick_relation`.
5. Score in two views:
   - **B view.** B's relation (DUPLICATE, RECURRENCE, NEW) against the gold pair label. Link F1 counts a DUPLICATE only when A is in B's group. Wrong-candidate rate = merged candidates that are not A / all merged candidates, over DUPLICATE calls.
   - **A view.** The relation between B and A only: DUPLICATE if A is in B's open group; RECURRENCE if A is closed and matched within 30 days; else NEW. This view matches what the gold label says.
6. Bootstrap: 1,000 resamples of pairs, seed 2026, 2.5% and 97.5% percentiles.
7. Tuning: grid of radius 25/50/75/100/150 m, window 6/12/24/48 h, transitivity on/off, BBL with or without the window. Pooled train+validation only (236 pairs). Objective: A-view DUPLICATE F1 with A-view false-merge at or below 0.15. Result: the same parameters as section 5 (25 m, 6 h, BBL keeps 48 h, transitivity off). Pooled A-view F1 0.92, false-merge 0.12. No parameter changed.
8. In the B view, no grid point reached a false-merge rate at or below 0.15. The reason is in 11.5.

Note: the test split was already read twice in session 1. The test rows here are a third read. They are not a clean held-out estimate.

#### 11.3 Results: A view (relation to the labelled A)

All 295 pairs:

| Strategy | n (dup) | A-rel acc [CI] | A-rel DUP F1 [CI] | A-rel REC F1 [CI] | A-rel false-merge [CI] | A in group (gold DUP) |
|---|---|---|---|---|---|---|
| exact_key | 295 (37) | 0.98 [0.96-0.99] | 0.93 [0.86-0.99] | 0.99 [0.96-1.00] | 0.08 [0.00-0.18] | 0.95 |
| radius_time | 295 (37) | 0.79 [0.74-0.83] | 0.69 [0.58-0.78] | 0.71 [0.60-0.81] | 0.48 [0.36-0.59] | 1.00 |
| fuzzy_address | 295 (37) | 0.79 [0.74-0.83] | 0.69 [0.58-0.78] | 0.71 [0.60-0.81] | 0.48 [0.36-0.59] | 1.00 |
| dbscan | 295 (37) | 0.71 [0.66-0.76] | 0.63 [0.53-0.72] | 0.63 [0.52-0.73] | 0.54 [0.43-0.64] | 1.00 |
| hdbscan | 295 (37) | 0.71 [0.66-0.76] | 0.63 [0.53-0.72] | 0.63 [0.52-0.73] | 0.54 [0.43-0.64] | 1.00 |
| graph | 295 (37) | 0.79 [0.74-0.83] | 0.69 [0.58-0.78] | 0.71 [0.60-0.81] | 0.48 [0.36-0.59] | 1.00 |
| hybrid_evidence (tuned) | 295 (37) | 0.94 [0.91-0.96] | 0.92 [0.85-0.98] | 0.86 [0.77-0.93] | 0.10 [0.02-0.21] | 0.95 |
| hybrid_legacy (old) | 295 (37) | 0.79 [0.74-0.83] | 0.69 [0.58-0.78] | 0.71 [0.60-0.81] | 0.48 [0.36-0.59] | 1.00 |

All 295 pairs without RT044:

| Strategy | n (dup) | A-rel acc [CI] | A-rel DUP F1 [CI] | A-rel REC F1 [CI] | A-rel false-merge [CI] | A in group (gold DUP) |
|---|---|---|---|---|---|---|
| exact_key | 108 (32) | 0.97 [0.94-1.00] | 0.95 [0.89-1.00] | 1.00 [1.00-1.00] | 0.06 [0.00-0.15] | 0.97 |
| radius_time | 108 (32) | 0.87 [0.80-0.93] | 0.85 [0.76-0.93] | 0.95 [0.89-1.00] | 0.26 [0.14-0.39] | 1.00 |
| fuzzy_address | 108 (32) | 0.87 [0.80-0.93] | 0.85 [0.76-0.93] | 0.95 [0.89-1.00] | 0.26 [0.14-0.39] | 1.00 |
| dbscan | 108 (32) | 0.84 [0.77-0.91] | 0.84 [0.75-0.92] | 0.92 [0.85-0.98] | 0.27 [0.15-0.40] | 1.00 |
| hdbscan | 108 (32) | 0.84 [0.77-0.91] | 0.84 [0.75-0.92] | 0.92 [0.85-0.98] | 0.27 [0.15-0.40] | 1.00 |
| graph | 108 (32) | 0.87 [0.80-0.93] | 0.85 [0.76-0.93] | 0.95 [0.89-1.00] | 0.26 [0.14-0.39] | 1.00 |
| hybrid_evidence (tuned) | 108 (32) | 0.96 [0.93-0.99] | 0.95 [0.89-1.00] | 0.98 [0.95-1.00] | 0.06 [0.00-0.15] | 0.97 |
| hybrid_legacy (old) | 108 (32) | 0.87 [0.80-0.93] | 0.85 [0.76-0.93] | 0.95 [0.89-1.00] | 0.26 [0.14-0.39] | 1.00 |

Test 59 pairs (third read):

| Strategy | n (dup) | A-rel acc [CI] | A-rel DUP F1 [CI] | A-rel REC F1 [CI] | A-rel false-merge [CI] | A in group (gold DUP) |
|---|---|---|---|---|---|---|
| exact_key | 59 (7) | 0.98 [0.95-1.00] | 0.92 [0.67-1.00] | 1.00 [1.00-1.00] | 0.00 [0.00-0.00] | 0.86 |
| radius_time | 59 (7) | 0.83 [0.73-0.92] | 0.70 [0.40-0.91] | 0.78 [0.50-0.95] | 0.46 [0.17-0.75] | 1.00 |
| fuzzy_address | 59 (7) | 0.83 [0.73-0.92] | 0.70 [0.40-0.91] | 0.78 [0.50-0.95] | 0.46 [0.17-0.75] | 1.00 |
| dbscan | 59 (7) | 0.76 [0.64-0.86] | 0.64 [0.35-0.86] | 0.70 [0.40-0.90] | 0.53 [0.25-0.79] | 1.00 |
| hdbscan | 59 (7) | 0.76 [0.64-0.86] | 0.64 [0.35-0.86] | 0.70 [0.40-0.90] | 0.53 [0.25-0.79] | 1.00 |
| graph | 59 (7) | 0.83 [0.73-0.92] | 0.70 [0.40-0.91] | 0.78 [0.50-0.95] | 0.46 [0.17-0.75] | 1.00 |
| hybrid_evidence (tuned) | 59 (7) | 0.92 [0.83-0.98] | 0.92 [0.67-1.00] | 0.78 [0.50-0.95] | 0.00 [0.00-0.00] | 0.86 |
| hybrid_legacy (old) | 59 (7) | 0.83 [0.73-0.92] | 0.70 [0.40-0.91] | 0.78 [0.50-0.95] | 0.46 [0.17-0.75] | 1.00 |

Test 59 pairs without RT044 (third read):

| Strategy | n (dup) | A-rel acc [CI] | A-rel DUP F1 [CI] | A-rel REC F1 [CI] | A-rel false-merge [CI] | A in group (gold DUP) |
|---|---|---|---|---|---|---|
| exact_key | 16 (5) | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 1.00 [1.00-1.00] | 0.00 [0.00-0.00] | 1.00 |
| radius_time | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |
| fuzzy_address | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |
| dbscan | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |
| hdbscan | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |
| graph | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |
| hybrid_evidence (tuned) | 16 (5) | 0.94 [0.81-1.00] | 1.00 [1.00-1.00] | 0.92 [0.73-1.00] | 0.00 [0.00-0.00] | 1.00 |
| hybrid_legacy (old) | 16 (5) | 0.88 [0.69-1.00] | 0.91 [0.67-1.00] | 0.92 [0.73-1.00] | 0.17 [0.00-0.50] | 1.00 |

#### 11.4 Results: B view (B's relation and group)

All 295 pairs:

| Strategy | B acc [CI] | B DUP F1 [CI] | B REC F1 [CI] | B NEW F1 [CI] | B false-merge [CI] | Link F1 [CI] | Wrong-candidate rate [CI] | Others per DUP call |
|---|---|---|---|---|---|---|---|---|
| exact_key | 0.79 [0.75-0.84] | 0.60 [0.50-0.70] | 0.28 [0.12-0.43] | 0.92 [0.89-0.94] | 0.56 [0.44-0.66] | 0.60 [0.50-0.70] | 0.99 [0.98-0.99] | 39.0 |
| radius_time | 0.29 [0.24-0.35] | 0.35 [0.27-0.44] | 0.12 [0.05-0.21] | 0.32 [0.25-0.39] | 0.79 [0.72-0.84] | 0.35 [0.27-0.44] | 0.98 [0.97-0.98] | 18.6 |
| fuzzy_address | 0.29 [0.24-0.35] | 0.35 [0.27-0.44] | 0.12 [0.05-0.21] | 0.32 [0.25-0.39] | 0.79 [0.72-0.84] | 0.35 [0.27-0.44] | 0.98 [0.97-0.98] | 18.6 |
| dbscan | 0.27 [0.22-0.32] | 0.32 [0.25-0.40] | 0.12 [0.04-0.21] | 0.29 [0.21-0.36] | 0.81 [0.75-0.86] | 0.32 [0.25-0.40] | 0.98 [0.97-0.98] | 17.2 |
| hdbscan | 0.26 [0.21-0.31] | 0.32 [0.25-0.40] | 0.11 [0.04-0.20] | 0.26 [0.19-0.33] | 0.81 [0.75-0.86] | 0.32 [0.25-0.40] | 0.98 [0.97-0.98] | 17.1 |
| graph | 0.29 [0.24-0.35] | 0.35 [0.27-0.44] | 0.12 [0.05-0.21] | 0.32 [0.25-0.39] | 0.79 [0.72-0.84] | 0.35 [0.27-0.44] | 0.98 [0.97-0.98] | 18.6 |
| hybrid_evidence (tuned) | 0.56 [0.50-0.61] | 0.52 [0.41-0.61] | 0.13 [0.05-0.21] | 0.71 [0.64-0.76] | 0.64 [0.55-0.73] | 0.52 [0.41-0.61] | 0.99 [0.98-0.99] | 31.7 |
| hybrid_legacy (old) | 0.29 [0.24-0.35] | 0.35 [0.27-0.44] | 0.12 [0.05-0.21] | 0.32 [0.25-0.39] | 0.79 [0.72-0.84] | 0.35 [0.27-0.44] | 0.98 [0.97-0.98] | 18.6 |

All 295 pairs without RT044:

| Strategy | B acc [CI] | B DUP F1 [CI] | B REC F1 [CI] | B NEW F1 [CI] | B false-merge [CI] | Link F1 [CI] | Wrong-candidate rate [CI] | Others per DUP call |
|---|---|---|---|---|---|---|---|---|
| exact_key | 0.65 [0.56-0.74] | 0.63 [0.52-0.73] | 0.18 [0.00-0.35] | 0.87 [0.78-0.94] | 0.54 [0.42-0.65] | 0.63 [0.52-0.73] | 0.99 [0.99-0.99] | 45.9 |
| radius_time | 0.46 [0.37-0.56] | 0.55 [0.44-0.64] | 0.16 [0.00-0.31] | 0.49 [0.32-0.63] | 0.62 [0.52-0.72] | 0.55 [0.44-0.64] | 0.99 [0.98-0.99] | 36.6 |
| fuzzy_address | 0.46 [0.37-0.56] | 0.55 [0.44-0.64] | 0.16 [0.00-0.31] | 0.49 [0.32-0.63] | 0.62 [0.52-0.72] | 0.55 [0.44-0.64] | 0.99 [0.98-0.99] | 36.6 |
| dbscan | 0.45 [0.36-0.55] | 0.54 [0.43-0.64] | 0.16 [0.00-0.31] | 0.47 [0.29-0.61] | 0.63 [0.53-0.72] | 0.54 [0.43-0.64] | 0.99 [0.98-0.99] | 36.3 |
| hdbscan | 0.44 [0.35-0.53] | 0.54 [0.43-0.64] | 0.16 [0.00-0.31] | 0.44 [0.26-0.59] | 0.63 [0.53-0.73] | 0.54 [0.43-0.64] | 0.99 [0.98-0.99] | 35.9 |
| graph | 0.46 [0.37-0.56] | 0.55 [0.44-0.64] | 0.16 [0.00-0.31] | 0.49 [0.32-0.63] | 0.62 [0.52-0.72] | 0.55 [0.44-0.64] | 0.99 [0.98-0.99] | 36.6 |
| hybrid_evidence (tuned) | 0.58 [0.49-0.68] | 0.60 [0.49-0.70] | 0.16 [0.00-0.32] | 0.76 [0.65-0.86] | 0.56 [0.45-0.67] | 0.60 [0.49-0.70] | 0.99 [0.99-0.99] | 43.4 |
| hybrid_legacy (old) | 0.46 [0.37-0.56] | 0.55 [0.44-0.64] | 0.16 [0.00-0.31] | 0.49 [0.32-0.63] | 0.62 [0.52-0.72] | 0.55 [0.44-0.64] | 0.99 [0.98-0.99] | 36.6 |

Test 59 pairs (third read):

| Strategy | B acc [CI] | B DUP F1 [CI] | B REC F1 [CI] | B NEW F1 [CI] | B false-merge [CI] | Link F1 [CI] | Wrong-candidate rate [CI] | Others per DUP call |
|---|---|---|---|---|---|---|---|---|
| exact_key | 0.85 [0.75-0.93] | 0.60 [0.27-0.84] | 0.22 [0.00-0.57] | 0.97 [0.92-1.00] | 0.54 [0.25-0.82] | 0.60 [0.27-0.84] | 0.99 [0.98-0.99] | 41.8 |
| radius_time | 0.32 [0.20-0.44] | 0.36 [0.17-0.55] | 0.09 [0.00-0.26] | 0.39 [0.21-0.55] | 0.78 [0.62-0.91] | 0.36 [0.17-0.55] | 0.98 [0.94-0.99] | 17.9 |
| fuzzy_address | 0.32 [0.20-0.44] | 0.36 [0.17-0.55] | 0.09 [0.00-0.26] | 0.39 [0.21-0.55] | 0.78 [0.62-0.91] | 0.36 [0.17-0.55] | 0.98 [0.94-0.99] | 17.9 |
| dbscan | 0.31 [0.19-0.42] | 0.33 [0.15-0.53] | 0.10 [0.00-0.29] | 0.36 [0.19-0.52] | 0.80 [0.64-0.92] | 0.33 [0.15-0.53] | 0.97 [0.94-0.99] | 16.6 |
| hdbscan | 0.31 [0.19-0.42] | 0.33 [0.15-0.53] | 0.10 [0.00-0.29] | 0.36 [0.19-0.52] | 0.80 [0.64-0.92] | 0.33 [0.15-0.53] | 0.97 [0.94-0.99] | 16.6 |
| graph | 0.32 [0.20-0.44] | 0.36 [0.17-0.55] | 0.09 [0.00-0.26] | 0.39 [0.21-0.55] | 0.78 [0.62-0.91] | 0.36 [0.17-0.55] | 0.98 [0.94-0.99] | 17.9 |
| hybrid_evidence (tuned) | 0.59 [0.47-0.71] | 0.55 [0.24-0.76] | 0.09 [0.00-0.26] | 0.76 [0.63-0.85] | 0.60 [0.33-0.85] | 0.55 [0.24-0.76] | 0.99 [0.98-0.99] | 36.5 |
| hybrid_legacy (old) | 0.32 [0.20-0.44] | 0.36 [0.17-0.55] | 0.09 [0.00-0.26] | 0.39 [0.21-0.55] | 0.78 [0.62-0.91] | 0.36 [0.17-0.55] | 0.98 [0.94-0.99] | 17.9 |

Test 59 pairs without RT044 (third read):

| Strategy | B acc [CI] | B DUP F1 [CI] | B REC F1 [CI] | B NEW F1 [CI] | B false-merge [CI] | Link F1 [CI] | Wrong-candidate rate [CI] | Others per DUP call |
|---|---|---|---|---|---|---|---|---|
| exact_key | 0.62 [0.38-0.81] | 0.62 [0.29-0.86] | 0.00 [0.00-0.00] | 1.00 [1.00-1.00] | 0.55 [0.25-0.83] | 0.62 [0.29-0.86] | 0.99 [0.98-0.99] | 49.3 |
| radius_time | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |
| fuzzy_address | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |
| dbscan | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |
| hdbscan | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |
| graph | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |
| hybrid_evidence (tuned) | 0.56 [0.31-0.81] | 0.62 [0.29-0.86] | 0.00 [0.00-0.00] | 0.89 [0.50-1.00] | 0.55 [0.25-0.83] | 0.62 [0.29-0.86] | 0.99 [0.98-0.99] | 49.4 |
| hybrid_legacy (old) | 0.44 [0.19-0.69] | 0.56 [0.25-0.80] | 0.00 [0.00-0.00] | 0.57 [0.00-1.00] | 0.62 [0.33-0.86] | 0.56 [0.25-0.80] | 0.99 [0.98-0.99] | 42.2 |

#### 11.5 Runtime (all 295 pairs, mean 42.6 candidates)

| Strategy | Mean ms per call | p95 ms | Errors |
|---|---|---|---|
| exact_key | 0.39 | 1.91 | 0 |
| radius_time | 0.54 | 1.83 | 0 |
| fuzzy_address | 0.56 | 1.95 | 0 |
| dbscan | 1.11 | 2.60 | 0 |
| hdbscan | 1.21 | 3.92 | 0 |
| graph | 37.98 | 177.88 | 0 |
| hybrid_evidence (tuned) | 0.58 | 2.06 | 0 |
| hybrid_legacy (old) | 35.63 | 181.01 | 0 |

`graph` and `hybrid_legacy` are about 60 times slower than the others. They score every candidate pair (O(n²)). The tuned hybrid does not, because transitivity is off.

#### 11.6 Lessons

- The one-candidate evaluation hid two bugs: the `hdbscan` crash and the BBL text mismatch.
- In the A view, the tuned hybrid and `exact_key` are the best. The old hybrid and the radius, fuzzy, cluster, and graph strategies stay at a false-merge rate of about 0.48 to 0.54.
- In the B view, every strategy looks bad. B is merged with many other candidates (median 16, up to 179 for `exact_key`). 54 of 79 `exact_key` DUPLICATE calls are RT120 noise. One building (655 E 230 St, BBL 2048330028) has up to 179 open noise reports in 48 h.
- The gold label is a pair label. It says how B relates to A. It does not say how B relates to the other candidates. When an open same-BBL report exists, B is often a real duplicate of that report, even when gold says NEW or RECURRENCE for A. Example: 27 gold RECURRENCE pairs become DUPLICATE in the B view, because another open report shares the BBL.
- So the B-view false-merge and wrong-candidate rates are upper bounds. They are not proven errors.
- Many open reports at one BBL is a chronic-location pattern. C07 handles that, not C05. C05 needs a rule for it, for example a cap on group size or a separate "chronic" flag.

#### 11.7 Limits

- Unlabelled candidates can be true duplicates. The wrong-candidate rate counts them all as wrong. The real rate is lower and unknown.
- The gold labels do not say B's relation to the whole set. The B-view accuracy is therefore not a fair score.
- The candidate window is 48 h back. Closed candidates older than 48 h (recurrence up to 30 days) are not in the context set. Only the labelled A covers that case.
- The test split has now been read three times.
- No pair has missing or weak coordinates. The fuzzy rule is still untested.
- One building and RT044 snow still dominate the data.
- Runtime is measured on one Mac (Apple laptop, Python 3.12). It is a relative number only.

#### 11.8 Draft CHANGELOG entry

```
### 2026-10-14 (session 2) - C05 multi-candidate evaluation and two fixes (proposal, not merged)
- Fixed: hdbscan TypeError from scikit-learn 1.9.1 epsilon_search with about 40+ points.
  Fallback: single-linkage cut at CLUSTER_EPS_M (same groups for min_samples=1).
- Fixed: usable_bbl now treats "2048330028.0" and "2048330028" as one key.
- Added: tools/c05_c06_eval.py multicand (all real candidates per pair; A view and B view; bootstrap CIs; runtime).
- Added: components/tests/test_c05_hdbscan_regression.py (3 tests). Suite: 24/24 pass.
- Result (A view, all 295 pairs): tuned hybrid DUP F1 0.92, false-merge 0.10; old hybrid DUP F1 0.69, false-merge 0.48.
- Found: in the B view, every strategy merges B with many unlabelled reports at chronic sites.
  Pair labels cannot score this. Needs set-level labels.
- Parameters unchanged after pooled train+val re-tuning (25 m, 6 h, BBL 48 h, transitivity off).
```

#### 11.9 Draft DATA_REGISTER entry

```
### 2026-10-14 (session 2) - C05 multi-candidate evaluation
- Input: data/processed/c05_label_kit/c05_labelled_pairs.csv (295 non-UNSURE pairs). Git-ignored.
- Input: data/processed/requests_real.parquet (49,636 rows). Git-ignored. Context: same type,
  48 h before B, within 500 m; OPEN/closed as of B's time. Mean 42.6 candidates per pair.
- Note: BBL is stored as float text in the label CSV and as integer text in the parquet.
- Output (local, not tracked): /tmp/irs-tune/out/multicand_results.csv, multicand_records.csv,
  multicand_grid_pooled.csv, multicand.log.
- Split: stratified on label, seed 2026. Tuning used pooled train+val only. Test read a third time.
- No data left the machine. DigitalOcean Spaces was not used.
```


### 12. Enhancements (2026-10-15, session 3)

Scripts: `tools/c05_c06_eval.py c05-exp`, `tools/c05_labelling.py candidate-sheet`. Outputs:
`/tmp/irs-tune/out/c05_e1_chronic_grid.csv`, `c05_e2_fuzzy_grid.csv`,
`c05_e2_synthetic_pairs.csv`, `c05_e3_cv_folds.csv`, `c05_enhance_results.csv`.
Tuning used pooled train+validation (236 pairs) or GroupKFold. The test split
is read again here (fourth read). Treat test numbers as indicative.

#### 12.1 E1 chronic-site rule (FAILED, kept off)

Method: when B links to at least `HYBRID_HOTSPOT_AT` open reports, keep only the
`HYBRID_GROUP_CAP` most recent ones. Grid: hot spot at 2/3/5/10/20, cap 1/2/3/5.
Rule for choice: lowest B-view wrong-candidate rate, with A-view DUPLICATE F1 no
more than 0.02 below the rule-off value and A-view false-merge at or below 0.15.

| Setting (pooled 236 pairs) | A-view DUP F1 | A-view false-merge | Wrong-candidate rate | Others per DUP call | A in group |
|---|---|---|---|---|---|
| Rule off | 0.92 | 0.12 | 0.99 | 30.9 | 0.97 |
| Hot spot 2, cap 1 | 0.16 | 0.57 | 0.92 | 0.9 | 0.10 |
| Hot spot 5, cap 3 | 0.42 | 0.31 | 0.93 | 2.2 | 0.30 |
| Hot spot 10, cap 5 | 0.49 | 0.27 | 0.95 | 3.2 | 0.37 |
| Hot spot 20, cap 5 | 0.49 | 0.27 | 0.96 | 4.3 | 0.37 |

Result: no setting passed. The cap removes most merges, but it keeps the most
recent reports, and the labelled A is usually not among them. The
wrong-candidate rate stays above 0.90. The rule stays in code with
`HYBRID_HOTSPOT_AT=None` (off). Lesson: recency is not a good way to pick the
real incident at a chronic site. A better rule needs set-level labels (see 12.4).

#### 12.2 E2 synthetic weak coordinates (fuzzy address measured)

Method: for every labelled pair, make three synthetic copies of B. All copies
drop the BBL on both sides. Tag: `is_synthetic=True`,
`label_source=synthetic_from_<source>`. The gold label is kept.
- `no_coords`: B has no coordinates.
- `jitter_blockface`: B moves 30 to 120 m in a random direction; `address_type=BLOCKFACE`.
- `no_house_number`: B has no coordinates and no house number.

Fuzzy cutoff grid on pooled synthetic pairs (708 rows):

| Fuzzy cutoff | A-view acc | A-view DUP F1 | A-view false-merge |
|---|---|---|---|
| off | 0.75 | 0.00 | 0.00 |
| 0.60 | 0.85 | 0.76 | 0.24 |
| 0.72 | 0.86 | 0.79 | 0.18 |
| 0.80 | 0.87 | 0.80 | 0.17 |
| 0.88 | 0.85 | 0.63 | 0.18 |
| 0.95 | 0.89 | 0.66 | 0.08 |

Chosen: 0.95 (the only cutoff with false-merge at or below 0.15).
All 295 pairs, by variant (95% CI, pair bootstrap):

| Strategy | no_coords F1 | jitter_blockface F1 [CI] / FM | no_house_number F1 | All variants F1 [CI] / FM [CI] |
|---|---|---|---|---|
| exact_key | 0.00 | 0.00 / 0.00 | 0.00 | 0.00 / 0.00 |
| radius_time | 0.00 | 0.00 / 0.00 | 0.00 | 0.00 / 0.00 |
| fuzzy_address | 0.00 | 0.57 [0.41-0.70] / 0.37 | 0.00 | 0.27 [0.18-0.36] / 0.37 [0.18-0.54] |
| hybrid_legacy | 0.00 | 0.57 [0.41-0.70] / 0.37 | 0.00 | 0.27 [0.18-0.36] / 0.37 [0.18-0.54] |
| hybrid tuned, fuzzy off | 0.00 | 0.00 / 0.00 | 0.00 | 0.00 / 0.00 |
| hybrid tuned, fuzzy 0.95 | 0.82 [0.69-0.91] | 0.82 [0.69-0.91] / 0.07 | 0.00 | 0.64 [0.56-0.72] / 0.07 [0.02-0.14] |

Test 59 pairs (fourth read), fuzzy 0.95, all variants: F1 0.55 [0.28-0.75], false-merge 0.00.

Result: APPLIED. `HYBRID_FUZZY_CUTOFF=0.95`. The real-label results do not change,
because no real pair has weak coordinates. Lessons: the old fuzzy rule did not
fire in the old score when coordinates were missing (score 0.40 < 0.45). A missing
house number defeats every cutoff. Text similarity of "E 230 ST" to "655 E 230 ST"
is below 0.95. That case needs a street-only match plus another signal.

#### 12.3 E3 group-by-building CIs and GroupKFold CV

Method: building key = BBL (A, else B), else coordinates rounded to 4 decimals.
178 buildings. The largest has 53 pairs (BBL 2048330028). CIs use a cluster
bootstrap: resample buildings, not pairs (1,000 resamples). CV: 5-fold
StratifiedGroupKFold by building (seed 2026). Each fold re-tunes radius
(25/50/150 m), window (6/48 h), and the BBL window rule on its training folds.

| Strategy (all 295, A view) | DUP F1 | F1 CI, pair bootstrap | F1 CI, building bootstrap | False-merge | FM CI, pair | FM CI, building |
|---|---|---|---|---|---|---|
| exact_key | 0.93 | 0.86-0.99 | 0.60-0.99 | 0.08 | 0.00-0.18 | 0.00-0.44 |
| hybrid_legacy | 0.69 | 0.58-0.78 | 0.26-0.86 | 0.48 | 0.36-0.59 | 0.24-0.85 |
| hybrid tuned | 0.92 | 0.85-0.98 | 0.56-0.98 | 0.10 | 0.02-0.21 | 0.02-0.50 |
| hybrid, re-tuned per fold (out-of-fold) | 0.92 | — | 0.56-0.98 | 0.10 | — | 0.02-0.50 |

Every fold chose the same parameters (25 m, 6 h, BBL keeps 48 h). Result: the
parameters are stable across buildings. The building CIs are about three times
wider than the pair CIs. Lesson: the earlier pair CIs were too narrow. One
building decides much of the result. The out-of-fold estimate needs no test read.

#### 12.4 Candidate-set labelling sheet (tool only)

`tools/c05_labelling.py candidate-sheet` writes
`data/processed/c05_label_kit/candidate_sets/c05_candidate_set_sheet.csv`
(git-ignored). 295 sets, 7,020 rows, at most 60 candidates per set plus A. One row
per (B, candidate) with distance, hours, same BBL, address similarity, and open
status. Column `label_same_incident` is blank (SAME / DIFFERENT / RECURRENCE /
UNSURE). No labels were made. Test: `components/tests/test_c05_candidate_sheet.py`.


### 13. E4 silver labels from NYC 311 duplicate notes (2026-10-15, session 4)

Code: `tools/c05_c06_eval.py c05-exp --pull` (download) and `--silver` (labels and eval).
No new files. Outputs: `/tmp/irs-tune/out/c05_e4_*.csv`. Silver pairs:
`data/processed/nyc311_open_data/c05_silver_pairs.csv` (git-ignored).

**Leakage rule.** `resolution_description` makes the silver LABEL only. The pair
table has no resolution column (the code asserts this). C05 never reads it.

#### 13.1 How often the city marks a duplicate

Marker pattern (case-insensitive): "duplicate", "already been reported",
"already reported", "previously reported", "previously filed", "existing
complaint / service request / request", "earlier complaint about the same
location", "open service request already exists".

Local table (`requests_real.parquet`, 49,636 rows): 2,778 marked rows (5.6%).
HEAT/HOT WATER RT080 1,670; Street Condition RT001 326; Water System RT020 190,
RT024 124, RT025 45; Rodent RT100 120; PLUMBING RT082 57; Dirty Condition 35;
Water Maintenance 60; Sewer and Sewer Maintenance 88; Dead Animal 21; Illegal
Dumping 21; Missed Collection 8; Snow or Ice 5. NYPD types (noise, parking)
have none. The local table is a sample of time windows, so most originals are
missing. So we pulled more data.

Public NYC Open Data 311 (dataset erm2-nwe9, Socrata, no key), 2026-01-01 to
2026-02-28, nine complaint types that use the marker. 202,860 rows after
catalog mapping:

| Complaint type | Rows | Marked duplicate | Share |
|---|---|---|---|
| Dead Animal | 1,050 | 67 | 6.4% |
| Dirty Condition | 9,177 | 265 | 2.9% |
| HEAT/HOT WATER | 138,959 | 50,402 | 36.3% |
| Illegal Dumping | 3,851 | 100 | 2.6% |
| PLUMBING | 18,402 | 1,297 | 7.0% |
| Rodent | 2,519 | 947 | 37.6% |
| Sewer | 3,050 | 455 | 14.9% |
| Street Condition | 12,054 | 1,245 | 10.3% |
| Water System | 13,798 | 5,602 | 40.6% |

#### 13.2 Silver labels (matching rule)

- B is marked by the city: A = an earlier report of the same `request_type_code`,
  created at most 7 days before B and OPEN at B's time. Same BBL first. Else the
  nearest one within 50 m. Ties: the most recent. Label DUPLICATE.
- B is not marked: A = the nearest earlier same-type report within 150 m and
  48 h (open or closed). Label NEW.
- One pair per B.

Result: 60,380 marked rows. 54,964 matched (DUPLICATE). 5,416 had no open
original in the data (dropped). 81,957 NEW pairs. 96% of the DUPLICATE pairs
share a BBL (median distance 0 m, median gap 3.5 h). 50% of the NEW pairs also
share a BBL.

#### 13.3 Agreement with the 295 AI-supervised labels

Method: compute silver labels on the local table for the same pairs. DUPLICATE if B
is marked and the rule picks the same A. NOT_DUP if B is not marked. Dropped if B is
marked but the rule picks another A (7 pairs).

| | Silver DUP | Silver NOT_DUP |
|---|---|---|
| Hand DUP | 1 | 36 |
| Hand NOT_DUP | 0 | 251 |

n = 288. Agreement 0.875. **Cohen's kappa 0.05.** Only 8 hand-pair B reports carry
a city marker. Most hand duplicates are NYPD noise (RT120) and snow (RT044), and
those agencies never write the marker. So the low kappa shows low coverage, not
a conflict. The two label sets measure different populations. The silver set is
not a check on the hand labels for noise and snow.

#### 13.4 C05 strategies on the silver set

Temporal split: January 2026 = train, February 2026 = test (60,802 pairs, 25,341
DUPLICATE). CIs: building bootstrap, 1,000 resamples. A small re-tune on January
(radius 25/50/150 m, window 6/48 h, BBL window rule) picked the same parameters
as before (25 m, 6 h, BBL keeps 48 h). No setting reached false-merge <= 0.15 on
January (best 0.38).

February test, all types:

| Strategy | Precision | Recall | DUP F1 [95% CI] | False-merge [95% CI] | Accuracy [95% CI] |
|---|---|---|---|---|---|
| exact_key | 0.64 | 0.88 | 0.74 [0.73-0.75] | 0.36 [0.35-0.37] | 0.66 [0.65-0.67] |
| radius_time | 0.47 | 0.92 | 0.62 [0.61-0.64] | 0.53 [0.51-0.54] | 0.41 [0.40-0.42] |
| fuzzy_address | 0.47 | 0.92 | 0.62 [0.61-0.64] | 0.53 [0.51-0.54] | 0.41 [0.40-0.42] |
| dbscan | 0.47 | 0.92 | 0.62 [0.61-0.63] | 0.53 [0.52-0.55] | 0.39 [0.38-0.40] |
| hdbscan | 0.47 | 0.92 | 0.62 [0.61-0.64] | 0.53 [0.51-0.54] | 0.41 [0.40-0.42] |
| graph | 0.47 | 0.92 | 0.62 [0.61-0.64] | 0.53 [0.51-0.54] | 0.41 [0.40-0.42] |
| hybrid_evidence (tuned, production) | 0.63 | 0.90 | 0.74 [0.73-0.75] | 0.37 [0.36-0.38] | 0.64 [0.63-0.65] |
| hybrid_legacy (old) | 0.47 | 0.92 | 0.62 [0.61-0.64] | 0.53 [0.51-0.54] | 0.41 [0.40-0.42] |
| hybrid re-tuned on January | 0.63 | 0.90 | 0.74 [0.73-0.75] | 0.37 [0.36-0.38] | 0.64 [0.63-0.65] |

February test, DUP F1 per complaint type [95% CI]:

| Complaint type | n (dup) | exact_key | radius_time | hybrid_legacy | hybrid tuned | tuned false-merge |
|---|---|---|---|---|---|---|
| Dead Animal | 72 (14) | 0.21 [0.00-0.41] | 0.33 [0.17-0.51] | 0.33 [0.17-0.51] | 0.41 [0.21-0.58] | 0.70 |
| Dirty Condition | 1565 (63) | 0.17 [0.04-0.29] | 0.12 [0.06-0.19] | 0.12 [0.06-0.19] | 0.18 [0.07-0.28] | 0.90 |
| HEAT/HOT WATER | 49862 (22195) | 0.80 [0.79-0.81] | 0.66 [0.65-0.67] | 0.66 [0.65-0.67] | 0.79 [0.78-0.80] | 0.31 |
| Illegal Dumping | 237 (11) | 0.10 [0.01-0.21] | 0.10 [0.04-0.17] | 0.10 [0.04-0.17] | 0.14 [0.05-0.27] | 0.92 |
| PLUMBING | 5110 (902) | 0.36 [0.31-0.41] | 0.29 [0.24-0.34] | 0.29 [0.24-0.34] | 0.36 [0.30-0.41] | 0.78 |
| Rodent | 194 (124) | 0.83 [0.66-0.91] | 0.84 [0.68-0.92] | 0.84 [0.68-0.92] | 0.87 [0.73-0.93] | 0.08 |
| Sewer | 212 (84) | 0.48 [0.31-0.67] | 0.77 [0.69-0.85] | 0.77 [0.69-0.85] | 0.74 [0.65-0.85] | 0.19 |
| Street Condition | 1060 (217) | 0.01 [0.00-0.02] | 0.39 [0.34-0.45] | 0.39 [0.34-0.45] | 0.32 [0.26-0.37] | 0.74 |
| Water System | 2490 (1731) | 0.49 [0.45-0.54] | 0.75 [0.72-0.77] | 0.75 [0.72-0.77] | 0.64 [0.60-0.67] | 0.19 |

#### 13.5 Lessons

- The tuned hybrid and `exact_key` are the best overall (F1 0.74). The old hybrid
  is 0.62. This agrees with the hand-label result, on a set made by the city.
- No strategy is safe on the silver set. The false-merge rate is 0.36 or more. But
  many silver NEW pairs are probably real duplicates. Half of them share a BBL.
  HPD only marks building-wide heat duplicates. So the silver NEW label is noisy,
  and the false-merge rate is an upper bound.
- Street assets have no BBL. For Street Condition, Water System, and Sewer, a
  radius rule beats the BBL rule (Water System 0.75 vs 0.64; Street Condition
  0.39 vs 0.32; `exact_key` 0.01). A per-type radius is the next experiment.
- Building-type complaints (HEAT/HOT WATER, PLUMBING, Rodent) favour the BBL rule.
- The matching rule uses BBL and distance. So the silver set favours BBL-based
  strategies a little. This is a known bias.

#### 13.6 Limits

- Two winter months only. HEAT/HOT WATER is 82% of the test pairs.
- Agencies use the marker in different ways. NYPD and DSNY snow do not use it.
- An unmarked report is not proof of NEW.
- 5,416 marked reports had no open original inside 7 days in our pull.
- The temporal split is one cut (January -> February), not a full CV.

### E5. Per-complaint-type radius and time (2026-10-15)

#### Method
- Data: the NYC 311 silver pairs from E4. Train is January 2026. Test is February 2026.
- C05 does not use resolution_description as a feature. It is used only for the label.
- For each request type code, we tried radius 0/10/25/50/75/100/150 m, time 6/12/24/48 h, and BBL with or without the time window (56 settings). Radius 0 means BBL (exact site) only.
- Fallback: a type with fewer than 30 January DUPLICATE pairs keeps the global 25 m / 6 h.
- Constraint: a type setting must not raise the January false-merge rate of that type above the global setting. An absolute limit of 0.15 is not possible on silver labels, because every strategy is above 0.27 overall.
- HEAT/HOT WATER (RT080) has 67,418 January pairs. We tuned it on a label-stratified sample of 8,000 pairs (seed 2026).
- CIs: building-grouped bootstrap, 1,000 resamples.

#### Chosen settings
| Type | Complaint | Radius m | Window h | BBL needs window |
|---|---|---|---|---|
| RT001 | Street Condition | 0 | 48 | no |
| RT020 | Water System | 25 | 12 | no |
| RT023 | Sewer | 25 | 48 | no |
| RT024 | Water System | 50 | 48 | no |
| RT025 | Water System | 0 | 6 | no |
| RT042 | Dirty Condition | 25 | 6 | yes |
| RT080 | HEAT/HOT WATER | 0 | 24 | yes |
| RT082 | PLUMBING | 0 | 24 | yes |
| RT100 | Rodent | 25 | 12 | no |

RT002, RT003, RT021, RT022, RT041, RT045, RT044, and all other types keep 25 m / 6 h.

#### February test (60,802 pairs, 25,341 DUPLICATE)
| Strategy | Acc A | F1 DUPLICATE | False merge |
|---|---|---|---|
| exact_key | 0.661 (0.65-0.67) | 0.742 (0.73-0.75) | 0.358 (0.35-0.37) |
| hybrid global | 0.642 (0.63-0.65) | 0.741 (0.73-0.75) | 0.368 (0.36-0.38) |
| hybrid per type | 0.702 (0.69-0.71) | 0.763 (0.75-0.77) | 0.276 (0.26-0.29) |

Per type, F1 DUPLICATE, global then per type: HEAT/HOT WATER 0.793 then 0.810; PLUMBING 0.358 then 0.385; Water System 0.637 then 0.703; Street Condition 0.319 then 0.513; Sewer 0.744 then 0.765; Dirty Condition 0.177 then 0.217; Rodent, Dead Animal, Illegal Dumping are the same. Full table: /tmp/irs-tune/out/c05_e5_per_type_results.csv.

#### Hand-label check (295 pairs)
- Per type and global give the same result: F1 0.921 (0.56-0.98), false merge 0.103 (0.02-0.50). Grouped 5-fold F1: 0.80, 1.00, 0.96, 0.67, 0.89 for both.
- Reason: most hand pairs are RT044 snow or other types that are not in the dict. This check shows no harm. It does not show a gain.

#### Decision
- We put the table in components/c05_duplicate.py as HYBRID_TYPE_PARAMS.

#### Lessons
- One radius does not fit all types. Building complaints (heat, plumbing) work best as BBL only. A 25 m radius merges different buildings.
- Street and water types need a longer window (12-48 h), because the city closes them slowly.
- Silver labels are noisy. Low absolute scores (for example Dirty Condition 0.22) show label noise and agency practice, not only detector error.

#### Limits
- One train month and one test month. Winter only. Seasonal types may change.
- The settings come from city labels, not hand labels.
- Dead Animal, Illegal Dumping, and Sewer have few test duplicates. Their CIs are wide.


---

# Part 3. C05 labelling log

Owner: Nabeel Taresh. Path: `documents/C05_C06_changelog/RESULTS.md`.
Short sentences. Times are AST (UTC+3). All dates are 2026-10-14.

### Disclosure

The labels are AI-made under human supervision. An AI labeller applied the
rules. Nabeel Taresh reviewed every batch and approved every decision.
The file records this as `label_source=hand_ai_supervised`. Each note starts
with "AI-made under human supervision (Nabeel)".

### Method

1. Read `C05-labelling-rules.md`. It is the base rule set.
2. Label in batches of 50 pairs, in `pair_id` order.
3. Use only facts known when B was filed: type, addresses, `address_type`,
   BBL, distance, time gap, channels, descriptor text, and `open_closed_a`.
4. Do not use `resolution_description`, outcome fields, `closed_at_b`,
   `open_closed_b`, or any model score.
5. `open_closed_a` is OPEN when `closed_at_a` is empty or later than
   `created_at_b`. It is A's state when B was filed.
6. Mark a hard case `[low]` in the notes. Use UNSURE only when you cannot decide.
7. Send to review: every UNSURE, every `[low]`, and a random 10% of the rest
   (seed 2026).
8. Nabeel reviews. A new rule question becomes a numbered decision (D1-D14).
   Re-check all earlier batches after each new decision.
9. Every note gives a reason and the decision tags it used, for example
   `[D3]` or `[base rule step 6]`.

### Sample

| Item | Value |
|---|---|
| Source table | `data/processed/requests_real.parquet` (partial build, 49,636 rows) |
| Build window | 2025-10-19 to 2026-10-11 (`created_at`) |
| Candidate pool | 277,275 pairs (same type and `sample_window_id`, 0-1500 m, < 14 days) |
| Sampler | `tools/c05_labelling.py sample --source processed --n 300 --overlap 50 --seed 2026` |
| Kit | `data/processed/c05_label_kit/` (not in git) |
| Pairs | 300 unique. 50 OVERLAP, 250 SOLO. |

### Batches

| Batch | Pairs | Review rows | Approved by Nabeel |
|---|---|---|---|
| 1 | PAIR-0001..0050 | 10 | 11:53 |
| 2 | PAIR-0051..0100 | 18 | 11:58 |
| 3 | PAIR-0101..0150 | 10 | 12:01 |
| 4 | PAIR-0151..0200 | 8 | 12:07 |
| 5 | PAIR-0201..0250 | 7 | 12:37 |
| 6 | PAIR-0251..0300 | 6 | 13:56 |

Review rows total: 59. Re-checks after D5-D8 changed 7 labels in batch 2
(0068, 0083, 0086, 0078, 0091, 0082 NEW -> DUPLICATE; 0089 UNSURE -> DUPLICATE).
Later re-checks changed no labels.

### Final labels

| Label | Pairs |
|---|---|
| NEW | 222 |
| DUPLICATE | 37 |
| RECURRENCE | 36 |
| UNSURE | 5 (dropped from scoring) |

The benchmark scores 295 pairs.

### Decisions

1. Noise where A was closed (e.g. by police) while the party continues: keep the base rule. A CLOSED at B creation -> RECURRENCE.
   Reason: base rule step 3 forbids DUPLICATE when A is CLOSED, and the closure time is the only thing known at B's creation; overriding it per type would leak outcome knowledge. Limitation: some RECURRENCE labels are probably the same ongoing party. Triggered by: 655 E 230 St pairs 0032-0050 (A CLOSED within minutes). Approved by Nabeel 2026-10-14.

2. Keep the 655 East 230 Street cluster (RT120; 41 pairs in batch 1, more in batches 2-3).
   Reason: removing pairs after sampling would bias the seed-2026 sample; the cluster is real 311 behaviour. Limitation: near-identical pairs from one building weigh heavily on kappa and the benchmark. Triggered by: 0001-0050 (41 of 50 from this building). Approved by Nabeel 2026-10-14.

3. Property-tied complaints (sidewalk snow etc.): neighbouring house numbers or a different BBL -> NEW.
   Reason: snow clearing is the duty of each property owner, so two adjacent lots are two separate jobs even 2-25 m apart. Triggered by: 0015 (147 vs 157 W Buchanan St), 0033 (56 vs 66 Marx St). Approved by Nabeel 2026-10-14.

4. Same address but different descriptor/problem -> NEW, marked [low] for review.
   Reason: "different problem" is listed in the base rules as NEW; multi-unit buildings host several unrelated complaints. [low] because the descriptor is coarse. Triggered by: 0007 (mold vs pests), 0012 (TV vs banging). Approved by Nabeel 2026-10-14.

5. 'Snow Tracking' and 'Sidewalk' descriptors = same problem (same place + A OPEN -> DUPLICATE; A CLOSED -> RECURRENCE).
   Reason: 'Snow Tracking' is a DSNY system category, not a different complaint, so applying decision 4 to it was wrong. Triggered by: 0068, 0083, 0086 (NEW -> DUPLICATE). Approved by Nabeel 2026-10-14.

6. Same BBL with different house numbers = same place.
   Reason: BBL is the tax lot; one lot can carry two street numbers. Triggered by: 0089 (40 vs 36 Hasbrouck Rd, same BBL; UNSURE -> DUPLICATE). Approved by Nabeel 2026-10-14.

7. Decision 3 applies only to property-tied complaints. Non-property-tied (road snow, water main, similar) on the same street within ~25 m can be DUPLICATE/RECURRENCE, marked [low].
   Reason: roads and water mains are city assets that span several addresses, so two neighbours can report one incident. Triggered by: 0078 (road snow, NEW -> DUPLICATE), 0075 (dirty water, stays DUPLICATE). Approved by Nabeel 2026-10-14.

8. Same object escalating (leaning -> fallen tree, apartment heat -> whole building) with A OPEN = DUPLICATE; A CLOSED -> RECURRENCE.
   Reason: a worsening condition of the same object is one incident for one crew; decision 4 is for unrelated problems. Triggered by: 0091 (tree), 0082 (heat) (NEW -> DUPLICATE). Approved by Nabeel 2026-10-14.

9. Identical INTERSECTION pins at 0 m with no BBL = same place, [low] (DUPLICATE if A OPEN, RECURRENCE if CLOSED). ADDRESS vs INTERSECTION pin stays UNSURE.
   Reason: with no BBL the shared intersection is the only location evidence; it is weak (base rule step 4), hence [low]. Triggered by: 0114 (Bismark Ave, both intersection). Approved by Nabeel 2026-10-14.

10. Street/sidewalk noise (not residential): > ~100 m -> NEW [low]; DUPLICATE [low] only if same street, <= ~50 m, <= ~30 min, A OPEN.
   Reason: street noise sources (cars, speakers) move and carry, so pins are less exact than for a building; a tight window limits false merges. Triggered by: 0131 (Amboy Rd, 133 m), then 0139, 0166, 0231, 0248. Approved by Nabeel 2026-10-14.

11. Decision 6 (same BBL = same place) applies only on the same street and within ~50 m.
   Reason: large complexes, condos and parks share one BBL across many buildings and streets. Triggered by: 0182 (2 Windham Loop vs 11 Kathy Pl, same BBL, 120 m). Approved by Nabeel 2026-10-14.

12. ADDRESS vs INTERSECTION pin: UNSURE only within ~75 m; beyond ~100 m -> NEW by the distance rule.
   Reason: at short range a weak pin cannot separate one place from two (base rule example 4); at long range the place clearly differs. Triggered by: 0183, 0165, 0156, 0159, 0167, 0172 (107-297 m); UNSURE kept for 0008, 0095, 0096, 0105, 0106. Approved by Nabeel 2026-10-14.

13. Decision 8 (escalation) for water mains only within ~25 m on the same street; otherwise NEW [low].
   Reason: aligns escalation with decision 7's range; water reports spread far along a main. Triggered by: 0200 (leak vs possible main break, 133 m). Approved by Nabeel 2026-10-14.

14. Decision 12's UNSURE applies only when the intersection includes A's street; if the street names clearly differ, label NEW.
   Reason: a nearby intersection of a different road is a different place; UNSURE should cover only genuine pin ambiguity. Triggered by: 0063 (3809 Amboy Rd vs Highland Rd intersection, 73 m; currently NEW). Re-check of batches 1-5: no label changes (0008, 0095, 0096, 0105, 0106 have an intersection on or naming A's street, or cannot be ruled out, so stay UNSURE). Approved by Nabeel 2026-10-14 12:37 AST.

### Quality check (overlap)

A second blind pass labelled the 50 OVERLAP pairs again.

| Item | Value |
|---|---|
| Pairs | 50 |
| Agreement | 100% |
| Cohen's kappa | 1.000 |
| Labels | 5 DUPLICATE, 42 NEW, 3 RECURRENCE |
| Disagreements | 0 |

Files: `data/processed/c05_label_kit/work/overlap_pass1.csv`, `overlap_pass2.csv`,
`c05_overlap_disagreements.csv`.

**Caveat.** Both passes used the same rules D1-D14. This measures rule
consistency. It does not measure independent human agreement. The overlap set
is 84% NEW, so kappa is sensitive to few cases.

### Limitations

- RT044 Snow or Ice is 191 of 300 pairs (64%). Results mostly reflect snow complaints.
- One building (655 East 230 Street, RT120) gives a large near-identical cluster
  (41 pairs in batch 1 alone). It weighs heavily on agreement and the benchmark.
- `free_text_a` and `free_text_b` hold the descriptor category only. They are
  not resident text. Labellers cannot compare problem detail.
- The sample has no long time-gap pairs. All pairs are in the short or medium bin.
- The table is a partial build (download, clean, synthetic, NLP). It covers
  2025-10-19 to 2026-10-11 and has 49,636 rows. The register said about 49,558.
  Aggregates and `write_sample` were not run.
- D1 keeps RECURRENCE when police closed A while a party continues. Some
  RECURRENCE labels are likely the same incident.

### Open items

- Do a human blind overlap pass. Two team members (for example L1 and L2)
  label the 50 OVERLAP pairs without the AI labels. Then run `tools/c05_labelling.py kappa`.
- Consider a per-type cap in the sampler to reduce the RT044 skew.


---

# Part 4. C06 fake-risk: method and current results

Owner: Nabeel Taresh. Locked field names from contract v1.0.0, now dataclasses
on the plug-and-play `components/` API. Component version 0.1.0.
Path: `components/c06_fake.py` (does not overwrite Sankalp’s `c06_credibility`).
`from components.c06_fake import run`.

Write this section into the final report. Short sentences. Plain words.

### Purpose

C06 scores how likely a request describes a problem that is not there. It can
lower queue rank or send a case to review. It never refuses a citizen. The
action enum has no REJECT.

### Inputs and outputs (contract)

Input: `RequestFacts`, C05 `DuplicateResult`, C07 `CredibilityResult`, and a
1-hour burst count from the store. Output: calibrated `unfounded_probability`,
`risk_band`, `recommended_action` (`PROCEED` / `LOWER_PRIORITY` /
`HOLD_FOR_REVIEW`), `reporter_burst_flag`, `contributing_factors`.

SAFE_DEFAULT: probability `None`, action `PROCEED`. A failure never
deprioritises anyone.

### How it works

| Method | What it does | Role |
|---|---|---|
| `rules` | Type base rate, history, burst, corroboration | Transparent guardrail |
| `logreg` | Logistic regression, Platt calibration on val | Readable coefficients |
| `gbm` | Histogram gradient boosting, then calibration | Nonlinear interactions |
| `hybrid_rules_logreg` | Blend of rules and logreg | **Production** |
| `logreg_no_burst` | Logreg without burst features | Ablation |

Production `run()` uses `hybrid_rules_logreg`. Switch with `strategy=` or
`C06_STRATEGY`.

Operating points (team can still change these):

- LOW: p < 0.28 → PROCEED
- MEDIUM: 0.28–0.55 → LOWER_PRIORITY
- HIGH: p ≥ 0.55 → HOLD_FOR_REVIEW
- Burst flag at 5 reports in one hour
- First-time reporters: do not HOLD on type base rate alone unless p ≥ 0.70

Corroboration always lowers the probability. That rule is hard-coded so a
model cannot reverse it.

### Labels and leakage

Train on `verified_outcome` in {GENUINE, UNFOUNDED}. Drop inconclusive rows.
**Never** use `resolution_description`, `outcome_class`, `closed_date`,
`resolution_hours`, `status`, or `hidden_honesty` as features.

`verified_outcome` is a **proxy**. “The crew did not find the condition” is not
the same as “the citizen lied.” NYPD noise and parking notes are often “no
evidence” because the event had already ended. Report that bias.

Reporter ids come only from the shared synthetic population
(`synthetic_reporter_links`, `synthetic_citizens`). We do not invent a new
citizen table. Burst counts use that same population.

### Why the hybrid is the production method

Rules alone are brittle. GBM can fit noise on a small sample and is harder to
explain. Logistic regression has signed weights we can write onto
`contributing_factors`. Calibration (Platt / sigmoid on the val split) makes
the probability safer for C11 guardrail G2. The rule layer still writes burst
and corroboration in plain language.

We pick by **Brier score and first-time FPR**, with a floor on recall for
high-urgency types, then AUC.

### Evaluation

Shared harness: `py tools/c05_c06_eval.py benchmark`. Same time `split` as the rest of
the project. Results: `research/experiments/c06_benchmark.csv`.

C06 metrics use real `verified_outcome` on the **full processed table** test
split (5390 decided rows, `data_origin=processed`, run 2026-10-15). That
label is still a **proxy** (`label_kind=real_verified_outcome_proxy`). Say so in
the report.

Headline (processed test split, 2026-10-15, after the cold-start rule):

| Method | n | AUC | Brier | F1 @ 0.28 | FPR first-time (p >= 0.28) | FPR first-time (action) | Urgent unfounded recall (p) | Urgent recall (action) | ms |
|---|---|---|---|---|---|---|---|---|---|
| rules | 5390 | 0.573 | 0.247 | 0.692 | 0.817 | 0.300 | 0.71 | 0.70 | 0.00 |
| logreg | 5390 | 0.629 | 0.236 | 0.690 | 0.825 | 0.300 | 0.73 | 0.72 | 0.73 |
| gbm | 5390 | 0.632 | 0.233 | 0.692 | 0.850 | 0.158 | 0.78 | 0.78 | 2.55 |
| hybrid_rules_logreg (production) | 5390 | 0.613 | 0.237 | 0.692 | 0.825 | 0.325 | 0.71 | 0.70 | 0.10 |
| logreg_no_burst (ablation) | 5390 | 0.613 | 0.238 | 0.691 | 0.825 | 0.300 | 0.72 | 0.71 | 0.78 |

"FPR first-time" is now a true FPR: flagged genuine first-time reports divided by
all genuine first-time reports. The old column divided by all first-time reports.
"Action" means `recommended_action` is not PROCEED, so it includes the cold-start rule.

On the full table, AUC is 0.57 to 0.63. The models are weak. GBM now has the
best AUC and Brier. First-time FPR (true, at p >= 0.28) is 0.82 to 0.85. The
old value 0.67 to 0.69 used the wrong denominator. With the cold-start rule, the
action FPR is 0.16 to 0.33. Urgent unfounded recall is
0.71 to 0.78. Both are worse than on the 163-row sample. The sample table
(2026-10-07) is replaced. Do not quote it.

Full file: `research/experiments/c06_benchmark.csv`.

### Experiments 2026-10-15 (session 3)

Scripts: `tools/c05_c06_eval.py c06-exp` (models, features, CV) and the harness
`tools/c05_c06_eval.py benchmark`. Outputs: `/tmp/irs-tune/out/c06_grid_test.csv`,
`c06_cv.csv`, `c06_final.csv`, `c06_harness_before.csv`. Data: processed table,
temporal split train 24,116 / val 5,787 / test 5,390 decided rows. Selection on val
only. Test is read once per configuration. Bootstrap: 1,000 resamples, seed 2026.

#### Metric fix

The old `fpr_first_time` column divided by ALL first-time reports, not by genuine
first-time reports. So it was not an FPR. The true FPR for production was 0.825,
not 0.674. The harness now writes `fpr_first_time` (true FPR),
`first_time_fp_share_old` (old value), `fpr_first_time_action`, and
`recall_urgent_unfounded_action` (action is not PROCEED).

#### Leakage finding (credibility priors)

The synthetic reporter links were built from `hidden_honesty`
(`data/pipeline/synthetic.py`: UNFOUNDED when `rng.random() > hidden_honesty`).
On the decided rows, `hidden_honesty` correlates with UNFOUNDED at -0.62. So any
feature from synthetic reporter history (credibility score, past unfounded rate,
first-time flag, and also burst) carries the label by construction. With these
features, AUC rises from about 0.64 to 0.855. That gain is NOT real. We did not
ship it. Real reporter history can still help, but it cannot be measured on this
data.

#### Model, feature, and calibration grid (test split, selected on val)

| Feature set | Model | Calibration | Test AUC | Test Brier | Note |
|---|---|---|---|---|---|
| F0 current | logreg | Platt | 0.632 | 0.235 | Baseline features |
| F0 current | logreg | isotonic | 0.630 | 0.236 | |
| F0 current | gbm | Platt | 0.637 | 0.233 | |
| F0 current | gbm | isotonic | 0.637 | 0.233 | Best clean val Brier for gbm |
| F1 no burst/dup | logreg | Platt | 0.614 | 0.237 | Burst and window corroboration removed |
| F1 no burst/dup | gbm | isotonic | 0.613 | 0.236 | |
| F2 added (time of day, night, train-only type base rate, channel, C05 BBL corroboration) | logreg | Platt | 0.630 | 0.235 | No gain |
| F2 added | gbm | isotonic | 0.636 | 0.234 | No gain |
| F0 hybrid (0.65 logreg + 0.35 rules) | — | Platt | 0.720 | 0.219 | Rules use synthetic credibility: leak |
| F3 added + credibility priors | gbm | isotonic | 0.855 | 0.151 | Leak by construction (see above) |
| rules only | — | — | 0.800 | 0.198 | Leak (synthetic credibility) |

Temporal CV (expanding window by month) and grouped CV (5 folds by citizen) on
train+val give the same order. AUC, mean (sd): F0 gbm 0.657 (0.033) temporal,
0.693 (0.008) grouped; F1 gbm 0.641 / 0.679; F2 gbm 0.660 / 0.698; F3 gbm
0.854 / 0.849 (leak).

Lessons: burst and window corroboration add about 0.02 AUC. Burst also uses
synthetic links, so part of that gain may be the same artefact. The added real
features give no gain. Isotonic and Platt are within 0.003 Brier. The clean models
are weak (AUC about 0.63).

#### Per-band thresholds and the urgent-recall constraint

Method: on val, set the urgent threshold to the highest value that keeps urgent
unfounded recall at or above 0.95. Set the returning-reporter threshold to the
best F1 value. Set the first-time threshold (cold start) to the lowest value at
or above it with first-time FPR at or below 0.30.

Result on test (clean F0 models): urgent recall is 0.85 to 1.00 (for example F0
gbm isotonic 0.88, F0 logreg isotonic 1.00). The constraint holds on val, but
not always on test (101 urgent unfounded rows). A lower urgent threshold also
means more LOWER_PRIORITY actions on high-urgency types. That conflicts with
the rule that C06 must not lower a hazard. So the per-band urgent threshold was
NOT applied. The team must decide.

#### Applied change: cold-start rule (production `hybrid_rules_logreg`)

A first-time reporter without a burst keeps PROCEED unless p >= 0.60
(`FIRST_TIME_LOWER_FLOOR`). The floor is the lowest value with first-time FPR at
or below 0.30 on val (val FPR 0.24). Test results (5,390 rows; 120 genuine
first-time; 101 urgent unfounded):

| Production on test | First-time FPR [95% CI] | Urgent unfounded recall [CI] | FPR all [CI] | Recall all [CI] |
|---|---|---|---|---|
| Before (flag at p >= 0.28) | 0.825 [0.76-0.89] | 0.713 [0.62-0.80] | 0.850 [0.84-0.86] | 0.961 [0.95-0.97] |
| After (action, cold start 0.60) | 0.325 [0.24-0.41] | 0.703 [0.61-0.78] | 0.827 [0.81-0.84] | 0.955 [0.95-0.96] |

The rule only moves actions toward PROCEED, so it is the safe direction. It cost
one urgent unfounded detection. First-time reports are only 2.7% of the test
split, so the overall FPR barely moves. FPR for all genuine reports stays high
(0.83), because most type base rates sit above 0.28. That is the next problem.

#### New test

`components/tests/test_c06_with_real_credibility.py` (3 tests): Sankalp's
`c06_credibility.assess` output goes straight into `c06_fake.run`. A new citizen
gets the cold-start rule. A bad history gives a higher probability than a good
history. An anonymous reporter is safe.

### Explainability

`contributing_factors` is a list of `Reason`. Type base rate, corroboration,
burst, and (for logreg) the largest signed coefficients. Messages are one
plain sentence. Citizens never see this score (system-context §6).

### Business

Fewer wasted trips to empty sites. Always report **genuine requests wrongly
deprioritised** next to any saving. C10 must not lower a hazard from this
score. C11 may scale priority by at most ×0.6, never to zero.

### Limitations

- Full `requests_real.parquet` (49,636 rows) is not in git. Models now train on it
  when it is local. CI uses the committed sample.
- No `synthetic_burst_requests` sample is committed. Burst is reconstructed
  from reporter links on a 1-hour window.
- Credibility at training time is mostly neutral, because C07 scores are not
  stored on the sample.
- Proxy labels will mis-call some genuine-but-gone events as unfounded.

### Technique group mapping

Decision Automation (action bands, never reject) plus Knowledge Discovery
(calibrated tabular models on real outcomes).
