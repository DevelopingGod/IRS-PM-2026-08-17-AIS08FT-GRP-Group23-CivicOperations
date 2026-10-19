# Results

Every number below was produced by the commands in `experiments.md`. Raw data:
`outputs/benchmarks/*.csv`; tables, statistics and figures: `outputs/benchmarks/analysis/`.
**These are simulation results** on real NYC311 request locations and arrival times, the real OSM road
network and *synthetic* crews. They are not operational validation (limitations.md §1).

## 0. Summary of findings

| Research question | Finding (held-out test episodes) |
|---|---|
| RQ1. Does the GA improve priority-aware coverage over greedy dispatch? | **Yes vs rank-first greedy**: +10 to +13 requests served per episode (+9–18 %), rank-weighted coverage +0.04 to +0.07 (p_Holm < 1e-6, all 5 scenarios). **Marginally vs travel-aware greedy**: served +0.4 to +2.1 (p_Holm < 0.05 only for capacity 1.2 and the legacy roster), rank-weighted coverage +0.01 to +0.02 (significant in all scenarios). |
| RQ2. How much travel is saved? | **vs rank-first greedy: −38 % to −48 %** total travel (−7.8 to −8.4 min per served request; GA lower in 50/50 episodes). **vs travel-aware greedy: none — the GA travels 2–10 % more** (negative result; the price of near-zero priority inversions). |
| RQ3. Does the GA protect rank order? | Avoidable priority inversions: GA 0.05–0.25 per episode, rank-first greedy 0 (by construction), **travel-aware greedy 87–100** (it bypasses on average 15–23 higher-ranked requests per episode). |
| RQ4. Behaviour under shortage | Coverage falls from 86 % (legacy roster) to 63 % at capacity 0.6; the GA's advantage over rank-first greedy is **largest under shortage** (+13.1 served at 0.6 and 0.8). Unserved requests at 0.6 are mostly `CAPACITY_USED_BY_OTHER_REQUESTS` (55 per episode), plus `NO_COMPATIBLE_CREW` (5.6) and `UNROUTABLE_LOCATION` (3.5). |
| RQ5. Dynamic replanning vs a static plan | Rolling GA vs *static GA plan + insertion of new requests*: **travel −72 min (−7.6 %)** and rank-weighted wait −3.2 min (both significant); +1.4 completed and +0.008 rank-weighted completed share (**not significant** after Holm). Cost: 51 vs 6 changed assignments per episode. |
| RQ6. Instability of replanning | The stability mechanism (change penalty + publish threshold + commit-at-departure) **halves plan churn** (51 vs 96 changed assignments per episode, p_Holm = 2e-4) with no detectable loss of quality. Rolling rank-first greedy reshuffles 5× more (276). |
| RQ7. Replanning interval | Hybrid 15 min (periodic + forced events) ≈ periodic 5 min in quality at 19 % less compute; periodic 30 min completes 1.6 fewer requests and makes high-ranked requests wait 15 min longer (both significant) but travels 29 min less. |
| RQ8. Sensitivity | Rankings of methods are unchanged across speed factors 0.5–1.0 and budgets 360–450 min; absolute coverage moves by ±4 pp. Rank-weight transform and λ_B matter a lot (validation grid, §1). |
| Optimality | On 60 tiny instances (6 requests × ≤ 2 crews, validation) the GA found the exhaustive optimum in **60/60**; rank-first greedy in 75 %, travel-aware greedy in 50 %. |
| Routing | A\* (straight-line and ALT) returned exactly Dijkstra's cost on 500 random pairs; ALT expands 12.8 % of Dijkstra's nodes and is 2.6× faster; heuristic bound holds on all 148,904 routing edges. |

## 1. Validation: objective selection (E0 + amendment A1, 44 validation episodes, capacity 0.8)

Figure: `analysis/tuning_scatter.png`; full grid: `analysis/tuning_table.md`; selection:
`analysis/tuning_selection.json`.

* On the pre-registered grid (λ_B ∈ {60, 120, 240}) every transform improved monotonically with λ_B and
  none met the integrity limit (≤ 2 avoidable inversions per 100 served; best 2.75). **Low λ_B is
  harmful**: at λ_B = 60 the GA serves 24–66 requests vs 89 for rank-first greedy, because far-away
  requests are judged not worth their travel. This is the "travel optimised, priority neglected" failure
  the brief warns about, and it is why λ_B cannot be set arbitrarily.
* After extending the grid (amendment A1) the rule selected the **lexicographic objective with log
  rank weights**: 104.7 served per episode, 0.00 inversions per 100 served, 9.2 travel min per served
  request (rank-first greedy: 88.9 / 0.00 / 17.0; travel-aware greedy: 103.7 / 74.3 / 8.9). The
  lexicographic variants of all seven transforms lie within 2 % of each other in served count; the
  transform matters mainly in weighted-sum mode.
* GA size: small (24 × 80) is within 0.33 % of the large variant's objective at 1.9 s vs 9.2 s mean
  wall time → frozen. No test run hit the GA time limit (mean 27 generations, stall-terminated).

## 2. Static plans on held-out test episodes (E1: 50 episodes × 5 rosters; GA seeds 0–4)

Figure: `analysis/static_comparison.png` (CIs there show between-episode spread; inference uses the
paired tests in `analysis/static_tests.md`). Means per episode:

| Roster (crews) | Method | Served | Served % | Rank-wtd coverage | Travel min | Travel/served | Avoidable inversions |
|---|---|---|---|---|---|---|---|
| cf0.6 (17) | Rank-first greedy | 71.1 | 53.9 | 0.59 | 1175 | 16.8 | 0.00 |
| | Travel-aware greedy | 83.9 | 62.8 | 0.64 | 669 | 8.3 | 99.9 |
| | **GA** | **84.3** | **63.0** | **0.66** | 733 | 9.0 | 0.13 |
| cf0.8 (21) | Rank-first greedy | 86.1 | 63.5 | 0.67 | 1434 | 16.8 | 0.00 |
| | Travel-aware greedy | 98.7 | 71.7 | 0.72 | 825 | 8.6 | 100.0 |
| | **GA** | **99.1** | **71.9** | **0.74** | 872 | 9.0 | 0.05 |
| cf1.0 (26) | Rank-first greedy | 98.0 | 71.4 | 0.74 | 1606 | 16.5 | 0.00 |
| | Travel-aware greedy | 109.8 | 78.1 | 0.78 | 888 | 8.3 | 89.3 |
| | **GA** | **110.3** | **78.4** | **0.80** | 923 | 8.5 | 0.08 |
| cf1.2 (30) | Rank-first greedy | 105.6 | 76.3 | 0.78 | 1738 | 16.5 | 0.00 |
| | Travel-aware greedy | 116.1 | 82.3 | 0.82 | 944 | 8.3 | 87.7 |
| | **GA** | **116.6** | **82.5** | **0.83** | 967 | 8.4 | 0.10 |
| legacy (27) | Rank-first greedy | 110.9 | 80.3 | 0.82 | 1776 | 16.0 | 0.00 |
| | Travel-aware greedy | 119.1 | 84.6 | 0.84 | 885 | 7.5 | 87.0 |
| | **GA** | **121.2** | **85.8** | **0.86** | 926 | 7.6 | 0.25 |

Paired tests, capacity 0.8 (others in `static_tests.md`, Holm-corrected over 60 comparisons):

| GA vs | Metric | Mean diff [95 % CI] | GA better / tie / worse | p_Holm |
|---|---|---|---|---|
| rank-first | served | +13.1 [10.7, 15.5] | 47 / 3 / 0 | 4e-8 |
| rank-first | travel min | −561 [−641, −488] | 50 / 0 / 0 | 1e-13 |
| rank-first | rank-weighted coverage | +0.066 [0.06, 0.08] | 47 / 3 / 0 | 4e-8 |
| travel-aware | served | +0.46 [0.02, 0.85] | 26 / 14 / 10 | 0.13 (n.s.) |
| travel-aware | travel min | **+47.6 [27.8, 68.5]** | 17 / 0 / 33 | 0.001 (GA worse) |
| travel-aware | rank-weighted coverage | +0.017 [0.01, 0.02] | 42 / 8 / 0 | 8e-8 |
| travel-aware | avoidable inversions | −99.9 [−164.5, −54.7] | 41 / 9 / 0 | 1e-7 |

**Interpretation.** Rank-first greedy respects the queue literally but wastes capacity on long
cross-borough trips (≈ 17 min of travel per job). Travel-aware greedy is efficient but bypasses
higher-ranked requests wholesale. The GA reaches the travel-aware level of coverage *while keeping the
rank order almost intact*; it does not beat travel-aware greedy on travel. Seed variation of the GA is
small (SD 0.25–0.39 served, 0.7–1.3 % of the objective; `static_seed_variation.md`). Mean GA run time
is 1.6 s per episode (all agencies), maximum 4.5 s.

## 3. Dynamic replay on held-out daytime test episodes (E2: 28 episodes × 7 policies × 2 seeds, capacity 0.8)

Execution is simulated (lognormal service CV 0.30, travel CV 0.15; simulated approvals and Crew Portal
updates). Figure: `analysis/dynamic_policies.png`; tests: `analysis/dynamic_tests.md`.

| Policy | Completed | Rank-wtd completed | Rank-wtd wait (min) | Travel min | Plans published | Changed assignments | Optimiser s |
|---|---|---|---|---|---|---|---|
| **GA hybrid 15 (default)** | 107.8 | 0.698 | 84.7 | 882 | 44.3 | 51.2 | 32.3 |
| GA hybrid 15, no stability | 107.9 | 0.698 | 83.4 | 878 | 45.8 | 95.7 | 32.4 |
| GA periodic 5 | 107.6 | 0.696 | 85.9 | 877 | 43.6 | 53.7 | 39.7 |
| GA periodic 15 | 106.8 | 0.693 | 91.3 | 869 | 22.1 | 42.2 | 18.0 |
| GA periodic 30 | 106.3 | 0.689 | 100.1 | 853 | 16.0 | 38.3 | 13.3 |
| Static GA plan + insertion only | 106.4 | 0.690 | 87.9 | 954 | 41.0 | 6.2 | 1.1 |
| Rank-first greedy rolling 15 | 98.2 | 0.661 | **77.6** | 1311 | 46.7 | 276.4 | 1.1 |

* **Locked commitments**: 0 violations in all 392 runs (asserted on every replan).
* **GA vs greedy rolling**: +9.7 completed (p_Holm 2e-4), −429 travel min, 5× less churn — but the
  greedy's top-ranked requests start 7.2 min earlier on average (p_Holm 2e-4): a negative result for the
  GA on the rank-weighted wait metric, caused by the lexicographic objective valuing *coverage* before
  *earliness*.
* **Dynamic vs static (+ insertion)**: dynamic replanning mainly buys travel efficiency (−7.6 %) and
  shorter waits; the coverage gain is small and not significant. A planner valuing stability over
  ~70 travel minutes per episode could reasonably prefer the insertion policy.
* Overtime (simulated duration overruns beyond the budget) occurred in 18 % of runs, ≤ 6 min on average:
  plans are feasible on estimates; noise creates small overruns.

## 4. Sensitivity (E3, E4: every third test episode, capacity 0.8)

| Speed factor φ | GA served | GA travel | Rank-first served | Rank-first travel |
|---|---|---|---|---|
| 0.50 | 104.9 | 1097 | 87.7 | 1731 |
| 0.60 (default) | 106.8 | 940 | 91.2 | 1521 |
| 0.75 | 108.4 | 770 | 95.7 | 1275 |
| 1.00 | 110.7 | 591 | 100.2 | 1026 |

| Budget (min) | GA served | Rank-first served |
|---|---|---|
| 360 | 99.6 | 83.2 |
| 420 (default) | 106.8 | 91.2 |
| 450 | 109.9 | 95.2 |

Travel time scales ~1/φ as expected; coverage changes by ±4 pp and the GA–greedy gap (+10 to +17
served) persists across all settings. Conclusions are therefore not an artefact of the uncalibrated
speed factor, though absolute times are.

## 5. Routing (E6)

| | Expanded nodes (mean) | ms per query (pure Python) |
|---|---|---|
| Dijkstra | 30,717 | 25.2 |
| A\* straight-line | 15,556 (50.6 %) | 22.6 |
| A\* ALT (8 landmarks) | 3,927 (12.8 %) | 9.6 |

500 random pairs at φ = 0.6: maximum relative cost difference to Dijkstra 0.0 (exact). Matrix
construction, 50 × 50 nodes: one-to-many Dijkstra 0.25 s vs pairwise A\* 38.4 s (identical values),
which justifies decision D-11. Network enrichment: 29.4 % of edges carry a valid OSM speed, 63.1 % use
the NYC 25 mph default, 7.4 % the class median, 0.07 % the fallback.

## 6. Demonstrations (`outputs/demos/`, summary in `demo_summary.json`)

* **D1** static proposal for a Manhattan test window (162 eligible requests, roster cf1.0): 138 planned,
  rank-weighted coverage 0.86; per-agency maps and per-crew panels (`maps/crew_panels_NYPD.png`).
* **D2** shortage: same window at capacity 0.6 (17 crews) vs 1.2 (28 crews): 96 vs 144 planned,
  rank-weighted coverage 0.66 vs 0.89; unserved reasons dominated by `CAPACITY_USED_BY_OTHER_REQUESTS`
  (65 vs 17).
* **D3** dynamic Staten Island window (68 real arrivals; 51 completed in simulation, 36 plan versions,
  60 maps): a new request that shifts C11 ranks and is
  absorbed by a published replan; replans with EN_ROUTE / IN_PROGRESS jobs locked; a simulated crew
  breakdown (forced replan, the only crew with a required skill lost → `NO_AVAILABLE_COMPATIBLE_CREW`);
  a cancellation; a held request; a simulated review completion that makes a request eligible. All
  evidence is cross-referenced to plan versions in `demo_summary.json`; maps show before/after.
* **D4** night window (00:03): NYPD crews plan immediately; all other agencies plan at 07:00 when C11
  is re-run (option C).

## 7. What was not verified

* No comparison with real NYC dispatch decisions or real completion times (none available).
* The full `c11_final` pipeline (C10 + official SLA tables) was not run; the replay used the vendored
  C11 fallback (limitations §6).
* GPU acceleration was not implemented, so there is no CPU/GPU benchmark.
* Cross-borough crew sharing, multi-day backlog and multi-crew jobs are outside the evaluated scope.
