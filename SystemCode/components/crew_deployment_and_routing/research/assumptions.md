# Assumptions register

Every number the component uses that is not observed data is listed here with its status.
"Research assumption" means: chosen for this academic prototype, not measured NYC practice,
evaluated by sensitivity where it matters.

| ID | Assumption | Value | Status / evidence | Sensitivity |
|---|---|---|---|---|
| A-PRI-1 | Priority input | `overall_queue_position` only | Project rule (C11 contract) | – |
| A-PRI-2 | Rank → weight transform | selected on validation (see results) | Dispatch modelling assumption | full grid on validation (E0) |
| A-OBJ-1 | λ_B (travel minutes worth one rank-1 request) | selected on validation from {60, 120, 240} | research assumption | E0 |
| A-OBJ-2 | λ_D (per weighted start-hour) | 10 | research assumption | small relative to λ_B by design |
| A-OBJ-3 | λ_C (per moved planned request) | 5 | research assumption | E2 ablation (λ_C = 0) |
| A-CAP-1 | Productive work-time budget | 420 min of a nominal 480-min day | research assumption (breaks, set-up, paperwork); not a measured NYC productivity rate | 360 / 450 (E4) |
| A-CAP-2 | Return to depot counted in budget | yes | research assumption | config switch |
| A-CAP-3 | No per-crew shifts; agency operating hours | default 07:00–19:00; NYPD 24 h with 480-min duty periods | design option C (agreed with the project owner); hours are not NYC policy | config |
| A-CAP-4 | Minimum remaining service period | 120 min, else plan for next opening | research assumption | config |
| A-SVC-1 | On-site service duration | `est_service_minutes` from `request_catalog.csv` | authoritative project catalog (an estimate, not measured durations) | dynamic replay adds noise (A-SIM-1) |
| A-SPD-1 | Valid OSM `maxspeed` used as posted speed | 5–70 mph valid; bare numbers = km/h | OSM tagging convention | – |
| A-SPD-2 | OSM list values (merged ways) | mean of valid values | assumption (length weights unavailable after simplification) | affects 155 edges |
| A-SPD-3 | NYC default limit for untagged non-motorway/trunk edges inside NYC | 25 mph | Verified: NYC default limit lowered to 25 mph effective 7 Nov 2014 (nyc.gov, Office of the Mayor, release 494-14; NYC DOT 25 mph notice) | – |
| A-SPD-4 | Posted → travel speed factor φ | 0.6 | **uncalibrated** research parameter: posted limits overstate urban travel speed (signals, congestion, turns not modelled) | 0.5 / 0.75 / 1.0 (E3) |
| A-SPD-5 | Untagged motorway/trunk/out-of-city edges | median tagged speed of the same class in this graph (motorway 50, motorway_link 40, trunk 30 mph…) | data-driven imputation | – |
| A-RTE-1 | Snap threshold | 500 m to the largest strongly connected component | research assumption | – |
| A-RTE-2 | No turn penalties / restrictions, no live traffic | – | OSM bundle has none | limitation |
| A-DATA-1 | NYC311 `created_date` is local New York time | America/New_York | consistent with the hour-of-day profile; NYC Open Data floating timestamps | – |
| A-DATA-2 | Episodes are independent; no backlog carried in | – | the extract is a sample of ~2-h windows, not a continuous stream | limitation |
| A-CREW-1 | Workforce sizing | crews(agency, borough) = max(1, round(cf · mean_train(demand / per-crew minutes))) | train split only; cf ∈ {0.6, 0.8, 1.0, 1.2} relative to *sampled-window* demand, not NYC staffing | four scenarios + legacy roster |
| A-CREW-2 | Travel allowance used only for sizing | 15 min per job | research assumption | – |
| A-CREW-3 | Depots | original synthetic depot coordinates reused (cycled when an agency-borough has more crews than depots) | synthetic | – |
| A-CREW-4 | Multi-skill crews | only skill combinations present in the original synthetic roster | synthetic | – |
| A-C11-1 | C11 fallback inputs (when `c11_final` is not present) | catalog `default_urgency` as the C10 urgency input to C11; no SLA points | documented proxy; disappears when the shared C11 folder is present | – |
| A-SIM-1 | Actual service time | lognormal, mean = estimate, CV 0.30 | simulation assumption | – |
| A-SIM-2 | Actual travel time | lognormal, mean = matrix estimate, CV 0.15 | simulation assumption | – |
| A-SIM-3 | Staff approval in replay | at departure, immediately, labelled simulated | simulation assumption | – |
| A-SIM-4 | Breakdown handling in replay | en-route job released by a simulated staff override; an in-progress job is finished first | simulation assumption | – |
| A-DYN-1 | Replan interval | 15 min (initial assumption) | – | 5 / 30 min (E2) |
| A-DYN-2 | Publish threshold | ≥ max(5, 1 % of F) unless forced | research assumption | E2 ablation |
| A-DYN-3 | Overdue buffer | 15 min | planning assumption for unconfirmed jobs | – |
