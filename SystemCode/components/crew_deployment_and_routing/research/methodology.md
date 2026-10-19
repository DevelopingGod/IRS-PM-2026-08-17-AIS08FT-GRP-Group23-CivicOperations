# Methodology

## 1. Problem

At a planning instant *t*, given C11's globally ranked open requests, a crew roster and the drivable
road network, propose for every crew an ordered list of future stops so that higher-ranked requests
are favoured, travel is low, work fits each crew's remaining work-time capacity, operational
commitments are untouched, and every request left unserved is explained. Unserved backlog is a
legitimate outcome, not a failure. Two AI techniques are used, each for a distinct job:

* **Evolutionary computing (GA)** — assignment of requests to crews and job sequencing (the business
  resource-optimisation decision).
* **Informed search (A\*)** — road-following shortest paths and travel times on the OSM graph.

## 2. Notation and constraints (one agency sub-problem)

Agency compatibility is a hard constraint, so the problem decomposes *exactly* into independent
per-agency sub-problems (no crew in the data belongs to two agencies).

* *R* eligible requests: `c11_queue_status = provisional`, valid rank, routable, not locked.
* rank *r_i* = `overall_queue_position`; weight *w_i = g(r_i)* (§4); service *s_i* (catalog estimate).
* crews *k* with skills *S_k*, planning origin *o_k* (current position or the site of its last
  locked job), depot *d_k*, availability *a_k* (minutes after *t* when locked work ends) and capacity
  *C_k = min(B_k − W_k − L_k, E_k − a_k)*: remaining productive budget (*B* budget, *W* confirmed
  worked minutes, *L* minutes reserved by locked jobs) and minutes left in the duty period *E_k*.
* travel time τ(u, v) from the A\*/Dijkstra-exact matrix (§5).

Hard constraints (enforced by construction in every method):
1. compatibility: request *i* only on crew *k* if agency matches and required skills ⊆ *S_k*;
2. each request at most once; locked/completed jobs are never in the decision;
3. capacity: τ(o_k, i_1) + Σ τ(i_j, i_{j+1}) + τ(i_L, d_k) + Σ s_i ≤ C_k (return-to-depot included);
4. eligibility by C11 status; crews unavailable or off duty receive nothing;
5. routability: only requests snapped to the largest strongly connected road component.

There are no request time windows, so the earliest start along a route equals the cumulative time.

## 3. Objective (minimised, minutes-equivalent)

F(x) = λ_B Σ_{i unserved} w_i + Σ_k travel_k + λ_D Σ_{i served} w_i · start_i / 60 + λ_C · Δ(x, x_prev)

| Term | Meaning | Why it is not double counting |
|---|---|---|
| λ_B Σ w_i (unserved) | priority-weighted backlog; λ_B = travel minutes worth serving a rank-1 request | decides *whether* a request is served |
| Σ travel | operating cost / efficiency | the only travel term |
| λ_D Σ w_i start_i | rank-weighted service-start delay (hours) | decides *when*, within the horizon, among served requests |
| λ_C Δ | number of previously planned (unlocked) requests moved to another crew or dropped | stability in rolling replanning only (0 in a first plan) |

Lexicographic priority (serve by rank first, then minimise travel) is the limit λ_B → ∞
(`objective.mode: lexicographic` sets λ_B = 10⁶) and is part of the sensitivity grid.

## 4. Rank-to-weight transform (Dispatch modelling assumption)

C11's position is ordinal. Candidates: reciprocal ρ/(ρ+r−1), exponential e^{−(r−1)/τ}, linear
1 − (r−1)/(N+1), and DCG-style 1/log₂(r+1). The transform and λ_B are selected on validation
episodes with a pre-registered rule (experiments.md §2) and the full grid is reported. Weights depend
only on `overall_queue_position`; C11's urgency, band and score are never read.

## 5. Informed search: routing and travel times

* **Network**: the shared OSM drive graph (59,251 nodes, 149,355 directed edges, one-way streets as
  directed edges), copied and enriched locally (`data/road_network/derived/nyc_drive_enriched.npz`).
  Parallel edges are collapsed to the fastest; self-loops dropped.
* **Edge cost**: c(e) = length_e / (posted_mph_e · 0.44704 · φ), φ = `routing.speed_factor`
  (posted→travel speed ratio, an explicit uncalibrated assumption; sensitivity 0.5–1.0).
* **Speed enrichment** (provenance per edge): valid OSM `maxspeed` → mean of a valid OSM list →
  NYC 25 mph default (non-motorway/trunk edges inside the city) → median tagged speed of the same
  road class → 25 mph fallback. See assumptions A-SPD-1…5.
* **Heuristics**: (i) straight-line h(n) = haversine(n, t) / v_max, admissible because every edge has
  length ≥ great-circle distance of its endpoints (verified for all 148,904 routing edges, minimum
  ratio 1.00000003) and speed ≤ v_max, and consistent by the triangle inequality;
  (ii) ALT (A\*, landmarks, triangle inequality) with 8 farthest-point landmarks, combined with (i)
  by `max` (the max of admissible, consistent heuristics is admissible and consistent).
  Equality with Dijkstra is tested on random pairs (tests + `experiment routing`).
* **Snapping**: nearest node of the largest strongly connected component (KD-tree on a local
  equirectangular projection; exact haversine snap distance recorded); > 500 m → unroutable.
* **GA efficiency**: A\* is *not* called inside fitness evaluation. For each agency sub-problem the
  engine builds one travel-time matrix over {requests, crew origins, depots} with one-to-many Dijkstra
  rows (cached per source node; faster than |V|² A\* queries for dense matrices — measured in
  `routing_matrix_benchmark`). Point-to-point A\* (ALT) reconstructs road-following legs of the
  *final* plan only (and locked legs for maps); each leg's A\* time equals the matrix entry (tested).

## 6. Evolutionary optimisation (formulation B)

Alternatives considered: (A) permutation chromosome + greedy decoder; (B) explicit route set +
unserved pool; (C) B with NSGA-II over (backlog, travel). B was selected (decision register D-03):
it maps directly onto locked prefixes, plan diffs and warm starts, and lets operators target
priority (swap-in of high-rank unserved requests).

* **Encoding**: one ordered list per crew; requests in no list are unserved.
* **Initial population**: rank-first greedy, travel-aware greedy, objective-aware rank-order fill,
  repaired previous plan (dynamic warm start), and fills in noisy rank order (lognormal noise) with
  random ruin-and-recreate.
* **Crossover** (route-based): each crew inherits its route from parent A or B; duplicates removed
  in random crew order (removal never breaks capacity, triangle inequality); remaining capacity
  refilled by cheapest feasible insertion in rank order, inserting only if F decreases.
* **Mutation** (one or two per child): relocate; inter-crew exchange; 2-opt; ruin-and-recreate
  (random or spatially related); swap-in (remove up to three lighter stops to fit a heavier unserved request).
* **Selection**: tournament (k = 3), generational with elitism (2); periodic 2-opt polish of the best.
* **Termination**: generations, stall generations, wall-clock safety limit (reported when hit;
  a run stopped by the clock is not bit-reproducible).
* **Feasibility**: every operator preserves the hard constraints, so no penalty weights for
  infeasibility are needed and every evaluated individual is a valid plan.

Baselines receive identical inputs: **rank-first greedy** (C11 order; append to the crew that can start
earliest; ties by added travel then crew id) and **travel-aware greedy** (repeatedly the feasible
insertion with smallest added travel per unit rank weight). An **exhaustive optimum** is computed for
tiny instances (≤ 8 requests) to measure the optimality gap.

## 7. Dynamic dispatch (rolling horizon)

* **Engine** = pure function of (snapshot, config, seed). **Controller** = state, events, versioning,
  audit. **Replay simulator** = deterministic discrete-event simulation of the surrounding pipeline
  (arrivals, C11 calls, approvals, Crew Portal updates, breakdowns) for research.
* **Locked commitments**: a job becomes locked at verified approval (`APPROVED_NOT_STARTED`), then
  `EN_ROUTE` / `IN_PROGRESS` by Crew Portal events. Locked and completed jobs are excluded from every
  re-optimisation; their remaining time is reserved and the crew's planning origin/availability move
  to the end of its locked work. In the replay, approval happens when a crew departs for its next
  stop (*commit at departure*): everything not yet departed stays re-optimisable.
* **Triggers** (policy `hybrid`, default): *forced* replans for a planned request cancelled/held/sent
  to review, a new eligible request outranking planned work of its agency (or an agency with nothing
  planned), crew unavailability with planned work, completion-time deviations ≥ 15 min, staff
  overrides and the start of a service period; all other changes (rank shifts, completions,
  arrivals that do not outrank planned work) are coalesced into the next periodic check (15 min).
  Arrivals are debounced (2 min) before C11 is called.
* **Publishing**: a non-forced replan is published only if F improves by ≥ max(5, 1 % of F_prev),
  where F_prev re-evaluates the previous plan's unlocked routes on the new snapshot; a replan that
  reproduces the current plan is never published. Together with λ_C and commit-at-departure this is
  the stability mechanism (ablated in E2).
* **Operating hours** (design option C): each agency has one operating window (default 07:00–19:00;
  NYPD 24 h with 480-minute duty periods). Requests known outside the window are planned at the next
  opening, when C11 is re-run. If less than 120 minutes remain before closing, planning targets the
  next opening.

## 8. Evaluation design

Episodes = sampled historical windows (train/val/test by the project's time split). Static plans are
computed at the end of each window with every request of the window known; dynamic replays stream the
real arrivals and simulate execution with lognormal duration noise (CV 0.30 service, 0.15 travel).
See experiments.md for the scenarios, metrics and statistical procedure.
