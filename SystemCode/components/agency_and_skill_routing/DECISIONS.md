# Decision register: Agency Assignment and Required Skills

Each decision lists what was decided, the evidence behind it and the alternatives
that were rejected. Figures come from the executed research outputs in
`outputs/research/` and `outputs/benchmarks/`. A re-run with `python run_all.py`
regenerates them, and the HTML report is rebuilt from them.

The register ends with three lists, kept separate: **confirmed observations**,
**assumptions** and **unresolved questions**.

---

## D-01 · The shared catalog is the operational reference

**Decision.** Agency and required skill(s) are looked up from
`request_catalog.csv` by exact RT code (rules R06 and R10). Nothing is hardcoded.
The CSV is read at start-up, so a catalog change needs no code change.

**Evidence.**
- 34 codes, no duplicates, no conflicts, no blanks. Each code maps to exactly one
  agency and one skill (`catalog_consistency.py`).
- The agencies (8) and skills (16) match the allowed values in the shared data
  reference exactly.
- Shared rule 3: "The request type code is the shared key. Agency, skill and time
  on site are looked up from the catalog with it."
- History agrees: the first agency equals the catalog agency in 49,556 of 49,558
  records (D-03).

**Rejected alternative.** Deriving the agency from the matrix or from history. Both
would create a second source of truth that other components do not share.

## D-02 · How the historical data was used

**Decision.** History is used offline, never at runtime, for three things:
1. Verifying the catalog against real routing.
2. Investigating referrals and ambiguity.
3. Deriving a small static knowledge table, `referral_knowledge.json`, fitted on
   the train split only.

The router does not need the Parquet file, pandas or pyarrow.

**Protocol.**
- The supplied `split` column was used as is. The splits are strictly ordered in
  time: train runs 2025-09-24 → 2026-05-20, validation → 2026-07-17 and test →
  2026-09-16.
- Train fits every data-derived quantity, validation selects, and test is read
  once.
- After-the-fact columns (`closed_date`, `resolution_description`,
  `outcome_class`, `verified_outcome`, `resolution_hours` and similar) are never
  runtime inputs. Training-period outcomes serve only as labels for the static
  knowledge.

## D-03 · Historical agreement is consistency, not accuracy

**Observation.** Rebuilding the historical RT code from `complaint_type` and
`descriptor` with the catalog's own matching columns reproduces 49,208 of 49,558
labels. The other 350 come from overlapping descriptor patterns, and none involve
a different agency. The historical RT label is therefore a function of the 311
complaint taxonomy.

**Consequence.** The 99.996% agreement shows that the catalog is consistent with
NYC 311's own routing. It does **not** measure end-to-end accuracy, which would
require real CO1 outputs and verified final agencies. The report and the
benchmarks call this metric "agreement with the historical first agency" and
never "accuracy".

## D-04 · No learned agency classifier

**Decision.** No classifier is trained to predict the agency.

**Evidence** (`method_comparison.py`, fitted on train, evaluated on val and test):

| Candidate | Changes vs catalog | Comment |
|---|---|---|
| C1 majority agency per RT code | 0 codes | identical to the catalog |
| C2 majority per RT code and borough | 0 cells | the only extra runtime field carries no routing signal |
| C3 "final agency" majority (from closure notes) | 1 code (RT002 → DEP) | rejected, see D-05 |

**Why each condition in the brief holds:**
- Every RT code uniquely determines the catalog agency.
- There is no reliable corrected-agency target (D-05).
- The features that explain referrals (descriptor, channel, plates, cause) are not
  available from CO1.
- A learned model would conflict with the shared catalog, and no correction
  policy has been agreed.

## D-05 · Interpretation of REFERRED requests

**Findings.**
- All 1,164 referred rows (719 train, 241 val, 204 test) have
  `verified_outcome = INCONCLUSIVE`.
- The closure note is written by the agency in `agency` in 100% of identifiable
  notes, so `agency` is the **initial** agency. For every referred row it equals
  the catalog agency.
- No structured destination column exists. Parsing the templated notes gives an
  explicit destination for 560 rows (48%):
  - DEP 278, NYPD 219, DOT 87, DSNY 15, DPR 9, NYCHA 6, MTA 6, DOB 3.
  - 63 are transfers to an internal unit of the same agency.
  - The rest say only "appropriate agency", "another agency" or "out of
    jurisdiction".

**Decision.** Referred records are **retrospective error indicators**. They are
neither positive nor negative examples of a correct agency, and no supervised
correction model is trained on invented targets.

**Why C3 (RT002 → DEP) was not adopted:**
1. It would override the shared catalog without an agreed policy.
2. The notes say DOT inspected, requested DEP to act, and re-inspects in 60 days.
   DOT is a legitimate first responder.
3. No runtime input separates the cases that move to DEP (45 of 64 determinable
   test cases) from those DOT finishes (19).
4. The label comes from parsed free text.

The finding is raised with the group instead (see the questions at the end).

## D-06 · Catalog/matrix conflicts

**Decision.** The matrix never overrides the catalog. It is used for:
1. Full agency names (glossary) in the output.
2. Explaining referral advisories ("depends on licence plates and location").
3. Documenting gaps.

**Findings.**
- The matrix names the catalog agency for 33 of 34 codes. RT045 (dead animal →
  DSNY) is not covered by any matrix row, which is a gap rather than a conflict.
- Eight codes sit in multi-agency families: RT043/RT124 (plates), RT080–RT084
  (private vs public housing, NYCHA) and RT121 (noise subtype). The context that
  decides between agencies is never in the CO1 contract, so contextual
  disambiguation is impossible at runtime.
- Eleven matrix agencies have no catalog code. Those requests are the intake
  bot's OTHER label.
- The mapping from code to matrix family is an analyst annotation
  (`research/matrix_family_map.csv`), because the matrix itself has no RT codes.

## D-07 · Unknown codes and catalog integrity

**Unknown codes.** A well-formed code that is not in the catalog (`RT999`) becomes
`UNRESOLVED` with no agency or skills (R07, R12), and `next_step = HUMAN_REVIEW`.
A malformed value (`POTHOLE`, `RT22`) is `INVALID` (R01).

**Catalog integrity** (R08) is checked once, at load time:

| Problem | Handling |
|---|---|
| Identical duplicate rows | collapsed, with a warning |
| Conflicting duplicates | `CONFLICT`, never resolved by picking a row |
| Blank agency or skill | `INCOMPLETE` |
| Malformed code | row skipped and reported |
| Unreadable `dispatchable` | treated as not dispatchable (fail-safe) |

Affected codes resolve to `UNRESOLVED` with `next_step = CATALOG_REVIEW`. With
`strict_catalog=True` the router refuses to start instead.

## D-08 · Special labels and emergencies

| Input | Status | next_step |
|---|---|---|
| `STOP_CALL_911` | BLOCKED | CITIZEN_TOLD_TO_CALL_911 |
| `CONFIRM_WITH_CITIZEN` | HELD | CONFIRM_WITH_CITIZEN |
| `is_emergency = true` with `CONTINUE` | HELD (contradiction) | HUMAN_REVIEW |
| `STOP_CALL_911` with `is_emergency = false` | BLOCKED + warning | CITIZEN_TOLD_TO_CALL_911 |
| label `EMERGENCY` | BLOCKED | CITIZEN_TOLD_TO_CALL_911 |
| label `OTHER` | HELD | ROUTE_TO_PERSON |
| label `SMALLTALK` | HELD | NO_REQUEST_CREATED |

**Note.** Shared rule 2 says EMERGENCY labels are "held". The label table in the
same document says "the citizen is told to call 911; nothing is filed". This
component chooses the safer reading, BLOCKED, and still sets
`needs_review = true`, so nobody is refused silently (shared rule 7). This is
listed as an open question.

## D-09 · Use of CO1 confidence

**Decision.** Confidence is passed through unchanged as `confidence`, its agreed
CO1 name (R09). It is the intake classifier's confidence, not the probability
that the agency is correct.

**Threshold.** The review threshold `min_confidence` is configurable and **off by
default**, because:
- CO1 already converts its confidence into `ready_to_submit` and `needs_human`;
- the historical data has no intake confidence, so no threshold could be
  calibrated here.

## D-10 · Dispatch eligibility

**Decision.** `dispatch_eligible` is true only when **all** of these hold (R13):
- the agency and skills are resolved;
- `ready_to_submit` is true;
- `needs_human` is false;
- `emergency_action` is `CONTINUE` with no contradiction;
- the label is not special;
- the catalog entry is dispatchable;
- no configured review policy fired.

**Mechanism.** Statuses combine by a fixed precedence
(INVALID > BLOCKED > HELD > UNRESOLVED > ASSIGNED). Rules can only add reasons,
so a blocking rule can never be cancelled. A parametrised test checks all 96
combinations of the gates.

**Limit.** Eligibility is necessary but not sufficient. Priority, location quality
and crew feasibility are checked by later components.

## D-11 · Estimated service time is excluded

**Decision.** The router never reads `est_service_minutes` or `default_urgency`
and never outputs them. A test asserts that the output contains no service-time,
urgency, priority, crew, route or coordinate fields.

**Reason.** Resolution time and urgency belong to other owners. Crew deployment
can read `est_service_minutes` itself using the same RT code, so there is one
source and no copy that can drift.

## D-12 · Historical referral advisory (R14)

**Decision.** Attach an explainable advisory built from train-period referral
rates. It is **informational by default**: it never changes the agency, the skills
or eligibility. The opt-in setting `review_on_elevated_referral_risk=True` makes
an ELEVATED advisory hold the request.

**Model selection** (validation only, rules fixed before reading test):
- Two-stage m-estimate tuning. RT+borough was kept over RT-only because it
  improved validation log-loss by 1.7% (above the pre-declared 1% bar) and raised
  AUC.
- The threshold of 0.15 maximised validation F1.

**Results on the untouched test split:**
- The advisory flags 3.70% of requests and contains 46.6% of later referrals.
- Precision is 34.5% against a 2.7% base rate, about 12.6×.
- AUC is 0.894, versus 0.883 for RT-only.

ELEVATED at code level: RT002, RT003 and RT043. RT124 is ELEVATED only in the
BRONX cell.

**Why not hold by default.** About 65% of flagged requests are handled by the
first agency. Holding them would delay legitimate work, and shared rule 7 says
nobody is refused automatically.

**Staleness.** The knowledge stores the hash of the catalog it was built against.
If the loaded catalog has a different hash, the knowledge is disabled.

## D-13 · Input strictness

**Decision.**
- Types are strict: the string `"true"` is not a boolean, `True` is not a
  confidence, and NaN is rejected.
- Only harmless normalisation is applied: case and whitespace of codes, actions
  and borough, each recorded as a warning.
- Unknown extra keys are ignored and not forwarded, so CO1 can add fields such
  as `slots.hazard_terms` without breaking this component.
- The router never raises on bad input. An internal error falls back to a safe
  HELD result (shared rule 6).

## D-14 · Slim hand-off to duplicate detection (agreed 15 October)

**Decision.** The output is one JSON object per request, in two parts:
1. **Passed through:** the eight agreed CO1 fields, under the same names and in
   the same order: `request_type`, `confidence`, `ready_to_submit`,
   `needs_human`, `is_emergency`, `emergency_action`, `emergency_category` and
   `slots`.
2. **Added:** `agency`, `required_skills`, `status`, `dispatch_eligible`,
   `next_step` and `explanation`.

**Details.**
- **No request identifier.** CO1 provides only the agreed fields, so none is
  required or forwarded. Batch results keep the input order.
- **Next step.** The next component in the shared pipeline is duplicate
  detection, so an `ASSIGNED` request carries `next_step = DUPLICATE_DETECTION`.
  Other statuses name where the request goes instead.
- **Invalid values.** A passed-through field that fails validation is forwarded
  as `null`, never as the invalid value. The explanation names the problem.
- **Audit detail.** Rules applied, structured findings, the referral advisory,
  versions, `needs_review`, `agency_resolution` and the full agency name are
  available only with `detail=True` (CLI `--detail`), in one `detail` object.

**Why.** The group asked for the output to focus on what downstream components
need, plus what was received from the previous component. Same field names
everywhere is the purpose of the shared data reference.

**Downstream use.** Crew matching at the end of the pipeline is
`crew.agency == agency` and `required_skills ⊆ crew.skills`. The mock consumer in
`tests/test_integration.py` checks this through a JSON round trip.

---

## Confirmed observations (executed)

- **Catalog:** 34 unique, complete codes; 8 agencies and 16 skills, matching the
  shared reference; all marked dispatchable.
- **Dataset:** 49,558 rows (train 34,690 / val 7,434 / test 7,434), strictly
  time-ordered, all REAL_311 and none synthetic. REFERRED = 1,164, matching the
  supplied guidance.
- **Agreement:** the catalog equals the historical first agency in 49,556 rows.
  The 2 exceptions are NYC311-PRD in train (RT003 and RT006, "Blocked –
  Construction").
- **Referrals:** `agency` is the initial agency; 48% of referrals name a
  destination; per-code referral rates are stable (Pearson 0.97 train vs val,
  0.96 train vs test).
- **Tests:** 297 tests pass. Every catalog code is tested, and all 20 required
  test topics are covered (see the report, section 18).
- **Safety:** on 57 labelled scenarios, plain lookup made 11 unsafe dispatches and
  the rule-based router made 0.

## Assumptions

- **CO1 contract:** the eleven agreed fields are `request_type`, `confidence`,
  `ready_to_submit`, `needs_human`, the four `slots`, `is_emergency`,
  `emergency_action` and `emergency_category`. `emergency_category` and the slots
  may be null; there is no request identifier.
- **Borough:** values outside the five boroughs are kept with a warning and do not
  block, because location is context for later components.
- **Analyst annotation:** the mapping from RT code to matrix family is an analyst
  annotation.
- **Perfect intake:** the historical replay assumes the RT code is correct. CO1
  misclassification is out of scope.

## Unresolved questions for the group

1. Should EMERGENCY labels be BLOCKED (this component) or HELD (shared rule 2
   wording)?
2. Should CO1 capture licence plates for vehicle requests (RT043/RT124), given the
   ~40% DSNY → NYPD referral rate?
3. Should RT002 (cave-in) and RT003 (defective hardware) notify DEP alongside DOT,
   or be split into sub-codes?
4. How should HPD codes (RT080–RT084) in NYCHA public housing be handled? NYCHA is
   not a catalog agency.
5. Can crew deployment record a structured destination agency when a job closes as
   REFERRED? That would enable a supervised correction model in future.
6. Who geocodes the text address? The shared reference leaves this open.
7. What CO1 confidence threshold, if any, should hold requests?
