# Experiments

All commands run from the component root with the component's own environment
(`.venv\Scripts\python -m crew_dispatch ...`). Results are written to `outputs/benchmarks/`.

## 1. Data separation (no temporal leakage)

| Split (project rule) | Period | Episodes | Used for |
|---|---|---|---|
| train | 2025-09 → 2026-05-20 | 202 | workforce sizing only (`crews_v2_cf*.parquet`) |
| val | 2026-05-20 → 2026-07-17 | 44 | rank-weight / λ_B / GA-size selection, exact-gap study |
| test | 2026-07-17 → 2026-09-16 | 50 | every reported comparison, frozen configuration |

An *episode* is one sampled ~2-hour borough window of the historical extract (a window that
straddles a split boundary is cut at the boundary, giving 296 episodes from 293 windows). After-the-fact
columns (`closed_date`, `resolution_description`, `outcome_class`, `verified_outcome`,
`resolution_hours`, plus `status`, `resolution_action_updated_date`, `log_resolution_hours`,
`resolution_valid`) are never copied into the component.

## 2. Pre-registered selection rules (written before the validation sweep was inspected)

**(a) Rank-weight transform and λ_B** — grid: reciprocal ρ ∈ {10, 25, 75}, exponential τ ∈ {25, 75},
linear, log × λ_B ∈ {60, 120, 240}; GA seeds {0, 1}; roster `cf0.8` (constrained); static plan at the
end of each validation window.

1. *Priority-integrity constraint*: keep configurations whose mean avoidable priority inversions
   are ≤ 2.0 per 100 served requests (an inversion is a higher-ranked unserved request that could
   have replaced a lower-ranked served one on the same crew without breaking capacity).
2. Among those, choose the highest mean number of served requests.
3. Ties (within 1 %) are broken by the lowest mean travel minutes per served request.
4. If no configuration satisfies (1), choose the lowest inversion rate.

Rationale: the brief asks to avoid both failure modes — optimising travel while neglecting
higher-ranked requests (guarded by rule 1) and rigid priority rules that waste capacity (rule 2/3).
The rule does not claim the chosen objective is uniquely optimal; the whole grid is reported.

**Protocol amendment A1 (validation only, made after inspecting the validation sweep).** On the
pre-registered grid every transform improved monotonically with λ_B and the best value (240) lay on the
grid boundary; no configuration met rule (1) (best: 2.75 inversions per 100 served). Because the optimum
was at the boundary, the λ_B grid was extended to {480, 960, lexicographic} for all seven transforms
(`experiment tuning_ext`, same episodes, seeds and roster) and the *same* rule was applied to the
combined grid. Test episodes were not touched before the configuration was frozen.

**(b) GA size** — small (24×80), default (40×150), large (60×250); choose the smallest variant whose
mean objective (seed-averaged, paired per episode) is within 0.5 % of the best variant.

## 3. Experiments

| ID | Command | Split | Question |
|---|---|---|---|
| E0 | `experiment tuning` (+ `tuning_ext`, amendment A1) | val | selection rules above |
| E1 | `experiment static` | test | RQ1/RQ2: GA vs rank-first greedy vs travel-aware greedy, capacity factors 0.6/0.8/1.0/1.2 + legacy roster; GA seeds 0–4 |
| E2 | `experiment dynamic` | test (daytime) | RQ3/RQ4: rolling replanning policies vs static plan + insertion, intervals 5/15/30 min, stability ablation; seeds 0–1 |
| E3 | `experiment speed` | test (every 3rd) | sensitivity to the posted→travel speed factor 0.5/0.6/0.75/1.0 |
| E4 | `experiment budget` | test (every 3rd) | sensitivity to productive budget 360/420/450 min |
| E5 | `experiment exact_gap` | val | optimality gap vs exhaustive optimum (6 requests × ≤2 crews) |
| E6 | `experiment routing` | – | A* (straight-line / ALT) vs Dijkstra; matrix backends |

**Fairness.** All methods receive the same C11 queue, crews, travel-time matrix, capacities and
constraints. The GA's initial population contains both greedy solutions (declared; it makes the GA
never worse than the baselines *on its own objective*, which is why the comparison is reported on
separate operational metrics, not on fitness alone).

**Statistics.** Paired per episode (GA metric averaged over seeds vs the deterministic baseline):
Wilcoxon signed-rank test, mean paired difference with a 95 % percentile bootstrap CI (10 000
resamples), Holm correction across the metric × scenario family. Seed variation of the GA is
reported separately.

**Definitions.** *Served (static)* = planned to start and finish within the crew's remaining capacity
in the current service period. *Completed (dynamic)* = a simulated Crew Portal completion event
occurred within the period. The two are never mixed.
