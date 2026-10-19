# Academic Decision Register: External Factors (Critical Facility Proximity)

Each decision lists the **alternatives**, **evidence**, **rationale**, **limitations** and **course
alignment** (DA = decision automation through business rules / knowledge-based reasoning;
KDDM = knowledge discovery and data mining; BRO = business-resource optimisation, which belongs
to Crew Dispatch and is deliberately *not* used here). All figures come from executed runs
(`outputs/tables/`, `outputs/benchmarks/`). No figure is invented.

---

### D1. Scope: critical-facility proximity only (weather removed)
- **Alternatives:** keep the weather sub-factor described in the 28 September shared reference; combine proximity with urgency.
- **Evidence:** the task prompt sets the current scope (weather removed). The shared reference lists the External Factors output as "to be named by Mohammad".
- **Rationale:** one well-defined geographic question gives a factor that is testable and easy to explain. The `wx_*` columns in the historical data are ignored (see `data_validation_summary.json`).
- **Limitations:** the shared reference PDF still mentions weather and needs a group update.
- **Course:** DA (a clean rule boundary).

### D2. Data source: OpenStreetMap
- **Alternatives:** NYC Open Data facility layers (DOE school points, DOHMH facilities, FDNY firehouses); commercial POI data.
- **Evidence:** OSM gave all required categories in one schema and one acquisition: 4,445 de-duplicated facilities, 4,580 features in total (`data/osm/manifest.json`). OSM keeps polygon footprints for 1,998 of 4,580 features (44%), which the official point layers usually lack. The road network for Crew Dispatch comes from the same source.
- **Rationale:** one open, versionable source (ODbL) with stable identifiers (`way/123`). Tag conventions were checked against the OSM wiki.
- **Limitations:** volunteer data is incomplete and uneven. For example, only 97 hospitals are tagged, and some may be specialised (psychiatric) facilities. No official completeness audit was done. Facility recall is unknown.
- **Course:** KDDM (data acquisition and quality assessment).

### D3. All five boroughs plus a 2 km buffer
- **Alternatives:** only the boroughs of interest; limit searches to the request's own borough.
- **Evidence:** `validate_osm.py` confirms all five OSM borough relations are present and that every core category exists in every borough. 404 de-duplicated facilities (359 in core categories) lie *outside* NYC inside the buffer. Test 10 (facility across a borough line) and test 11 (facility outside NYC) pass. On the real cache, a Staten Island example found its nearest facility in Carteret, NJ.
- **Rationale:** proximity is physical. Borough lines (and the state line) do not block an ambulance route or a school crossing. The buffer (2,000 m) is more than the ruleset needs (threshold 100 + maximum evaluable uncertainty 250 + coverage tolerance 100 = 450 m). The evaluator checks this when it loads.
- **Limitations:** OSM borough relations include water, so "inside NYC" is administrative rather than land-only. Marble Hill is labelled BRONX in the 311 data but lies in Manhattan's OSM boundary (53 of the 58 borough mismatches).
- **Course:** KDDM (coverage validation).

### D4. Distances in metres in a projected CRS (EPSG:32118)
- **Alternatives:** raw degree differences; haversine on a sphere; geodesic distance for every pair; UTM 18N.
- **Evidence:** across 3,000 request-facility pairs, the maximum difference from WGS84 geodesic distance was 0.099 m (0.0012%), and at most 0.002 m within 250 m (`benchmark_results.json`). Naive degree subtraction overstates a 0.01° longitude step at 40.75°N by 31.8%.
- **Rationale:** EPSG:32118 (NAD83 / New York Long Island) is the metric State Plane zone built for NYC. Planar distances are exact enough and enable vectorised GEOS queries.
- **Limitations:** the CRS is NYC-specific. Reuse elsewhere needs a different metric CRS (configurable through `metric_crs`).
- **Course:** knowledge representation (correct spatial semantics).

### D5. Straight-line, not network, distance
- **Alternatives:** walking or driving network distance; travel time.
- **Rationale:** the question is whether a request is *geographically adjacent* to a facility, not how long a crew takes to get there. Network travel time belongs to Crew Dispatch and Routing (BRO: A\*, Dijkstra), which reuses the prepared road graph.
- **Limitations:** a facility across a river or a highway can be close in straight-line terms but socially or physically separated.
- **Course:** a clear separation between DA (here) and BRO (dispatch).

### D6. Polygon geometry preserved; distance to the geometry, not the centroid
- **Alternatives:** convert everything to centroids; use points only.
- **Evidence:** 1,998 of 4,580 features are polygons or multipolygons. Test 05 shows a request 100 m from a polygon edge but 150 m from its centroid, which only the boundary distance flags correctly. Test 04 gives 0 m for a request inside a hospital footprint. The real Mount Sinai example is inside the footprint (0 m).
- **Rationale:** centroids misstate adjacency for large campuses (hospitals, school grounds). GEOS distance to a (Multi)Polygon is 0 inside, otherwise to the nearest boundary.
- **Duplicates:** OSM often maps a facility twice (POI node plus building). The rules D1 (point within 15 m of a same-category polygon, compatible name), D2 (polygon ≥95% inside a same-category polygon) and D3 (same-name points within 15 m) flagged 135 duplicates. They are kept for audit (`is_duplicate`, `duplicate_of`) but excluded from evaluation. Duplicates never reduce the nearest distance, so the binary flag does not change. Only counts and evidence lists do.
- **Limitations:** point-only facilities (for example, 171 of 351 fire-station features) are measured to the node, which typically sits at the building centre. This overstates the distance by roughly half the building depth.
- **Course:** knowledge representation.

### D7. Spatial indexing (STRtree per category)
- **Alternatives:** brute-force distance to all facilities; a single index for all categories.
- **Evidence:** 27x faster than brute force with identical results (max difference 0 m). On average 0.36 candidates per request survive the 100 m search, compared with 3,803 facilities checked by brute force. Single-request latency p50 is 0.144 ms; batch throughput is about 20.6k requests/s on the development machine.
- **Rationale:** per-category trees support category-specific thresholds and "nearest per category" evidence.
- **Course:** efficient knowledge-base querying.

### D8. Explainable rules rather than a learned model
- **Alternatives:** an ML classifier for "relevant proximity"; a weighted proximity score.
- **Evidence:** there is no independent ground-truth label of "facility-relevant hazard", and outcome fields (`outcome_class`, etc.) are after-the-fact and do not measure proximity relevance.
- **Rationale:** a declarative ruleset (`config/proximity_rules.yaml`) with rule identifiers, a structured evidence record and natural-language explanations gives a traceable binary decision. All 49,558 historical evaluations have a non-empty explanation and rule list, and every positive carries facility evidence (`holdout_summary.json`).
- **Course:** DA (business rules / knowledge-based reasoning).

### D9. Pure geographic design (Design A) selected; context-aware gating kept as opt-in
- **Alternatives:** Design B, context-aware: (B1) category-specific radii, (B2) add transit stations, (B3) zero the factor for building-interior request types.
- **Evidence (validation, `configuration_comparison_val.csv`):** A flags 26.2%. B1 (emergency sites at 150 m) adds 197 requests (agrees with A 97.3%). B2 (transit) adds 233 (96.8%). B3 removes 306 positives (95.8%): 16% of A's positives are interior housing or elevator requests.
- **Rationale:** A is the simplest, fully request-type-independent and easy to interpret. B3 is spatially plausible (interior conditions are not in the public right of way), but it uses request type, which already drives Urgency. Adopting it unilaterally risks double counting. It is implemented (`decision_mode: CONTEXT_AWARE`) and tested, ready if the Urgency owner agrees. B1 has no outcome evidence that emergency sites need a wider radius. B2 behaves as a density proxy, because subway stations are so dense.
- **Limitations:** A flags facilities that may be irrelevant to some complaints (for example, an apartment heat complaint next to a school).
- **Course:** DA, with explicit interface design.

### D10. How double counting with Urgency is prevented
- In `PURE_GEOGRAPHIC` mode the request type never enters the computation (test: RT005 and RT080 at the same point give identical evidence and factor).
- The output contains no urgency level, weight, score, rank or dispatch field. A recursive key scan in the tests enforces this.
- Both `critical_facility_nearby` (pure geographic evidence) and `external_factor` (agreed final factor) are returned, so Urgency decides how proximity interacts with request type.
- Rule identifiers name the facility category that triggered (for example, `CRITICAL_PROXIMITY_SCHOOL`), never a severity.

### D11. Candidate thresholds and how they were evaluated
- **Candidates:** 50, 75, 100, 150, 200, 250, 300, 400, 500, 750, 1000 m. These are experimental values from immediate surroundings to a broad neighbourhood, not NYC policy.
- **Train exploration:** the median nearest-core distance is 155 m. Trigger rate rises from 12.3% (50 m) to 28.8% (100 m), 48.4% (150 m), 73.8% (250 m) and 95.0% (500 m). Elasticity exceeds 1 up to 150 m. Schools are the sole trigger for about two thirds of flagged requests. A 15 m displacement flips 13% of positives at 100 m and 6% at 150 m.
- **Protocol:** `config/threshold_selection_protocol.yaml` was fixed after train exploration and before any validation proximity result. It defines an admissible range (75–150 m) from the operational definition and from geocoding error, a knowledge-based prior (100 m), and criteria C1–C5 (decision agreement ≥95% and positive stability ≥85% under 15 m error, train-validation shift ≤5 pp, trigger rate ≤50%, borough max/min ratio ≤3).
- **Course:** KDDM (sensitivity analysis, temporal validation).

### D12. Final threshold: 100 m, universal across the six core categories
- **Evidence (validation):** trigger rate 26.2% (train 28.8%, shift −2.6 pp), decision agreement 96.5%, positive stability 92.6%, borough ratio 2.95. All five criteria pass. 75 m fails C5 (ratio 3.30). 150 m fails C3 (shift −6.2 pp).
- **Hold-out (test, after freezing):** 26.3% (shift +0.1 pp vs validation), agreement 96.2%, positive stability 92.3%, ratio 2.62. All within the pre-declared tolerances.
- **Operational meaning:** "on the same block frontage, directly across the street, or at the adjacent intersection" (NYC short block ≈ 80 m, avenue right-of-way ≈ 30 m).
- **Status:** a knowledge-based heuristic, empirically checked. It is **not** statistically optimal and **not** an official NYC threshold, because no outcome labels exist. The borough ratio (2.95) sits close to its 3.0 bound.
- **Course:** DA + KDDM.

### D13. Incomplete evaluations are null, never silently 0
- **Alternatives:** default to 0; default to 1 ("safe" under shared rule 6).
- **Evidence:** 2.5% of historical requests are incomplete: 1,157 with missing coordinates and 75 where the location uncertainty spans the threshold.
- **Rationale:** a 0 claims "no facility nearby", which is false when the location is unknown. `external_factor = null` with `incomplete_reason` and `needs_review = true` keeps this honest. Shared rule 6 ("a failing component falls back to a safe default that does not lower anyone's score") is respected, because null contributes nothing. If Urgency needs an integer, `to_integer_factor(result, null_policy)` requires an explicit, agreed policy (`ZERO_FLAGGED` or `ONE_FLAGGED`) and flags every imputed value.
- **Location uncertainty:** an interval rule is used. With uncertainty u ≤ 30 m the supplied point decides (POINT_ESTIMATE). With 30 < u ≤ 250 m the factor is set only when the whole uncertainty disc lies inside (margin ≥ u) or outside (margin < −u) the threshold. Otherwise it is null. With u > 250 m, or postal-code/borough precision, the result is INCOMPLETE. Default uncertainties per precision class are heuristic. Under a 120 m displacement, 24% of decisions flip at 100 m, which is why coarse locations cannot use the point estimate.
- **Course:** knowledge-based reasoning under uncertainty.

### D14. Road network prepared for future reuse (no routing here)
- **Rationale:** Crew Dispatch and Routing needs a drivable graph. Downloading it once, with the same boundaries, CRS conventions and manifest, avoids duplicate acquisition and keeps the components consistent. No routing, A\*, Dijkstra or travel-time logic is implemented here. Missing attributes (for example, sparse `maxspeed`) are documented in `OSM_DATA_README.md` for later enrichment.
- **Status:** the road download was still incomplete at packaging time (8/18 sub-queries; every public Overpass server was overloaded or down). It is resumable with one command and shipped as a separate bundle. External Factors does not depend on it.
- **Course:** a shared data layer for BRO (the separate component).

### D15. Static visualisation separated from business logic
- **Rationale:** maps and charts (`visualization/`, `research/`) only read cached data and the evaluator's outputs. The runtime package has no plotting, OSMnx or network dependency (test 29b checks that importing the evaluator loads neither `osmnx` nor `requests`). Static PNGs are reproducible and embeddable in the report. No live dashboard is built.

### D16. Temporal split and leakage discipline
- **Evidence:** the splits are chronological and do not overlap (train 2025-09-24 to 2026-05-20, val to 2026-07-17, test to 2026-09-16). `closed_date`, `status`, `resolution_*`, `outcome_class`, `verified_outcome` and `resolution_hours` are dropped as soon as the data load. Live inputs carrying them are ignored with a warning (tested).
- **Rationale:** train for exploration, validation for selection, test touched once after freezing. No supervised training occurs, and no outcome is used to manufacture proximity labels.
- **Limitation:** the sample is borough-stratified (about 10k per borough), so citywide rates are not volume-weighted to real 311 traffic.
