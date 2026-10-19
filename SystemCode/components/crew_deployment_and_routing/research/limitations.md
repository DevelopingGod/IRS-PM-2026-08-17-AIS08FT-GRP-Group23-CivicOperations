# Limitations

1. **No dispatch ground truth.** NYC311 records do not say which crew served a request, in which order,
   with what travel time, or how long the work took on site. All results are *simulation* results on
   real request locations, real arrival times and the real road network; they are not operational
   validation. `resolution_hours` and other closure fields are deliberately unused (after-the-fact data).
2. **Synthetic workforce.** Crew numbers, skills mixes and depots are synthetic. Capacity factors
   0.6–1.2 are relative to the demand of the *sampled* windows (train split), not NYC staffing levels.
   Some small agency × borough × skill cells have no crew in some rosters (reported as
   `NO_COMPATIBLE_CREW`), an artefact of rounding small demand, which we keep visible rather than hide.
3. **Sampled episodes, no carried backlog.** The historical extract consists of ~2-hour borough
   windows on different days, not a continuous stream. Episodes start with an empty backlog and use
   only one borough's crews; cross-borough help and multi-day backlog are not evaluated.
4. **Travel-time uncertainty.** Edge speeds are posted limits (29 % observed in OSM, the rest imputed:
   NYC 25 mph default or class medians) scaled by an *uncalibrated* factor φ = 0.6. No turn penalties,
   signals, congestion by time of day or live traffic. Results are reported for φ ∈ {0.5, 0.6, 0.75, 1.0}.
5. **Service-time uncertainty.** On-site durations are catalog estimates; the replay perturbs them with
   assumed lognormal noise. Real durations by request type are unknown.
6. **C11 in the replay.** The full `c11_final` (with C10 and official SLA tables) was not available as a
   runnable folder; the replay therefore used the vendored `c11_prioritization.prioritize` with the
   catalog's default urgency as the C10 input and no SLA points. Rankings in the replay are thus
   coarser than the production C11 would produce; Dispatch's behaviour does not depend on how C11 ranks,
   only on the positions it returns. In the replay no request is `hold` or `provisional_review`
   unless scripted for the demo (C10 decides those in the full pipeline).
7. **Approval workflow.** Proposal-only mode. In the replay, staff approvals are simulated at the moment
   a crew departs; real approval latency, rejections and partial approvals are not modelled.
8. **Operating hours.** One window per agency (07:00–19:00; NYPD 24 h) is an assumption, not agency
   policy. Real crews work shifts, overtime and emergencies that this model does not represent.
9. **Single-crew jobs.** A request is served by one crew that holds all required skills; jobs needing
   several crews, equipment constraints, or crew skill levels are out of scope.
10. **GA guarantees.** The GA is a heuristic: it is never worse than the greedy solutions it is seeded
    with *on its own objective*, and close to optimal on tiny instances (exact-gap study), but there is
    no optimality guarantee on realistic instances. The objective weights are research choices
    selected on validation data, not a uniquely correct definition of "best".
11. **Rank weights.** Converting an ordinal rank into a weight is a modelling assumption; different
    transforms change which trade-offs the optimiser makes (reported in the validation grid).
12. **Wall-clock limits.** GA runs have a safety time limit; a run that hits it is not bit-reproducible
    (it is reported in the diagnostics when it happens).
13. **Maps.** Static snapshots for published, materially changed plans (capped per run); at most eight
    crews are coloured per panel. They show proposals, not real vehicle positions.
