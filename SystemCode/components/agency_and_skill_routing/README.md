# CivicOps AI: Agency Assignment and Required Skills

This is a standalone, plug-and-play component. It receives the structured output
of the **CO1 intake bot** and decides:

- the responsible NYC **agency**;
- the required **crew skill(s)**;
- an assignment **status**: `ASSIGNED`, `HELD`, `BLOCKED`, `UNRESOLVED` or `INVALID`;
- whether the request may proceed toward **dispatch**, and the **next step** (normally duplicate detection);
- a human-readable **explanation**, with the business rules and evidence used.

It uses explicit **business rules** (R01–R14) over the team's shared
`request_catalog.csv`. It also attaches an optional **historical referral
advisory**, mined from the 311 history and fitted on the training split only.

The runtime needs **only the Python standard library (3.9+)**.

It does **not** do any of these, because other components own them:
- intake classification;
- emergency detection;
- urgency or priority;
- service or resolution time;
- crew assignment or routing.

> **Research conclusion.** History shows that the RT code alone determines the
> agency (99.996% agreement; no learned model changed any assignment). The only
> thing history adds is a warning that some codes are often referred onward:
> RT043, RT002 and RT003. The component reports that warning without overriding
> the catalog. See `DECISIONS.md` and `reports/agency_skills_report.html`.

---

## 1. Quick start

Run these from the package root, which holds this README:

```bash
python run_demo.py
```

```bash
python -m civicops_agency assign examples/inputs/valid_co1.json
```

No installation is needed for either command. To install the package and get the
`civicops-agency` command:

```bash
python -m pip install .
```

```bash
civicops-agency demo
```

To run the tests:

```bash
python -m pip install pytest
```

```bash
python -m pytest
```

## 2. Python API

```python
from civicops_agency import AgencySkillRouter, RouterConfig

router = AgencySkillRouter.from_catalog("data/reference/request_catalog.csv")

co1_output = {
    "request_type": "RT022",
    "confidence": 0.96,
    "ready_to_submit": True,
    "needs_human": False,
    "is_emergency": False,
    "emergency_action": "CONTINUE",
    "emergency_category": None,
    "slots": {"incident_address": "41-17 MAIN STREET", "cross_street": None,
              "borough": "QUEENS", "landmark": None},
}

result = router.assign(co1_output)    # dict, JSON string or bytes
print(result.status.value, result.agency, result.required_skills, result.dispatch_eligible)
print(result.to_dict())               # slim hand-off to duplicate detection
print(result.to_dict(detail=True))    # + one "detail" audit block
print(result.to_json())

results = router.assign_batch([msg1, msg2, msg3])      # independent results, input order kept
json_text = router.assign_json('[{...}, {...}]')       # JSON in, JSON out
```

### Locating files

| What | How it is found |
|---|---|
| Catalog | `from_catalog(path)` takes any path. `AgencySkillRouter.default()` uses `$CIVICOPS_CATALOG`, then `data/reference/request_catalog.csv`. |
| Agency names | Read from `NYC311_agency_matrix.md` next to the catalog (`matrix_path=None` disables them). |
| Referral advisory | Read from `referral_knowledge.json` next to the catalog (`knowledge_path=None` disables it). |

The catalog is deliberately **not** embedded in the Python package. It is the
group's shared file and changes by agreement. Point the router at the current
copy.

### Configuration (`RouterConfig`)

| Option | Default | Effect |
|---|---|---|
| `min_confidence` | `None` | Hold requests whose CO1 confidence is below this value (R09). Off by default; see DECISIONS D-09. |
| `review_on_elevated_referral_risk` | `False` | Make an ELEVATED historical referral advisory hold the request (R14). |
| `normalize_request_type` | `True` | Trim and upper-case codes, with a warning. |
| `strict_catalog` | `False` | Refuse to start if the catalog has conflicts or incomplete rows. |
| `use_referral_knowledge` | `True` | Attach R14 advisories when the knowledge file exists. |
| `allow_stale_knowledge` | `False` | Use knowledge built for a different catalog version. |

## 3. Command-line interface

**`assign`** routes one object, an array, or JSON Lines; `-` reads stdin:

```bash
civicops-agency assign examples/inputs/batch_co1.json -o results.json
```

```bash
civicops-agency assign requests.jsonl --compact --fail-on-invalid
```

Policy options:

```bash
civicops-agency assign examples/inputs/referral_advisory_co1.json --review-elevated-risk --min-confidence 0.7 --detail
```

**`catalog-check`** prints the integrity report. It exits 1 if the catalog is unusable:

```bash
civicops-agency catalog-check --catalog path/to/request_catalog.csv
```

**`rules`** prints the business-rule register, and **`demo`** runs the bundled examples and shows CO1 input next to router output:

```bash
civicops-agency rules
```

```bash
civicops-agency demo
```

`python -m civicops_agency …` works the same without installation.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | OK |
| 1 | Catalog unusable |
| 2 | Unreadable input file or invalid JSON document |
| 3 | `--fail-on-invalid` was given and at least one request was INVALID |

## 4. Input contract (from CO1)

The input carries **no agency, no skills and no status**. The router finds the
agency and skills itself from `request_type`, using the catalog. These are
exactly the fields agreed with the intake owner:

| Field | Type | Notes |
|---|---|---|
| `request_type` | str | `RTnnn`, or `EMERGENCY` / `OTHER` / `SMALLTALK` |
| `confidence` | number 0–1 | passed through unchanged |
| `ready_to_submit`, `needs_human`, `is_emergency` | bool | strict booleans |
| `emergency_action` | str | `CONTINUE` / `CONFIRM_WITH_CITIZEN` / `STOP_CALL_911` |
| `emergency_category` | str or null | set by the emergency check |
| `slots.incident_address`, `slots.cross_street`, `slots.borough`, `slots.landmark` | str or null | nested `slots` object or flat `slots.x` keys |

There is no request identifier in the contract. Any other key is ignored and is
not forwarded. Invalid input never raises an exception; it returns
`status = INVALID`, and the explanation names the problem.

`examples/inputs/` holds sample CO1 messages. `examples/outputs/` holds what the
router returns for each one; regenerate them with
`python examples/generate_outputs.py`.

## 5. Output schema

The output is the agreed CO1 fields **passed through under the same names**,
followed by **six fields added by this component**. Everything goes to the next
component, duplicate detection.

```json
{
  "request_type": "RT022",
  "confidence": 0.96,
  "ready_to_submit": true,
  "needs_human": false,
  "is_emergency": false,
  "emergency_action": "CONTINUE",
  "emergency_category": null,
  "slots": {"incident_address": "41-17 MAIN STREET", "cross_street": "SANFORD AVENUE",
            "borough": "QUEENS", "landmark": null},

  "agency": "DEP",
  "required_skills": ["SEWER_DRAINAGE"],
  "status": "ASSIGNED",
  "dispatch_eligible": true,
  "next_step": "DUPLICATE_DETECTION",
  "explanation": "RT022 (Street flooding) was resolved through the shared catalog to DEP (New York City Department of Environmental Protection) with required skill SEWER_DRAINAGE. All intake, emergency and catalog checks passed, so the request continues to duplicate detection. Later components still decide priority, location and crew feasibility."
}
```

| Added field | Meaning |
|---|---|
| `agency` | Catalog agency for `request_type`; `null` when it cannot be justified. |
| `required_skills` | Catalog skill list; `[]` when unresolved. Never partial. |
| `status` | `ASSIGNED` (all gates passed) · `HELD` (waits for a person or the citizen) · `BLOCKED` (911 path) · `UNRESOLVED` (no justified agency or skill) · `INVALID` (contract violation). Precedence: INVALID > BLOCKED > HELD > UNRESOLVED > ASSIGNED. |
| `dispatch_eligible` | `true` only if `status == ASSIGNED`. Necessary, not sufficient: priority, location and crew checks come later. |
| `next_step` | `DUPLICATE_DETECTION` when assigned. Otherwise where it goes instead: `HUMAN_REVIEW`, `CONFIRM_WITH_CITIZEN`, `CITIZEN_TOLD_TO_CALL_911`, `ROUTE_TO_PERSON`, `NO_REQUEST_CREATED`, `FIX_INPUT` or `CATALOG_REVIEW`. |
| `explanation` | Plain-English reason, naming every blocking rule. |

A passed-through field that failed validation is sent on as `null`, so an
invalid value is never forwarded. The output never contains service time,
urgency, priority, crews, routes, coordinates or external factors.

### Optional detail block

`result.to_dict(detail=True)`, `assign_json(..., detail=True)` or the CLI flag
`--detail` add one `detail` object for auditing and research. The slim fields
above are unchanged.

| `detail` field | Meaning |
|---|---|
| `schema_version`, `component_version`, `catalog_version` | Output schema (2.0), package version, catalog content hash. |
| `decision_method` | `CATALOG_RULE_BASED`. |
| `agency_resolution` | `RESOLVED`, `UNKNOWN_CODE`, `CATALOG_CONFLICT`, `INCOMPLETE_CATALOG_ENTRY`, `SPECIAL_LABEL` or `NOT_ATTEMPTED`. |
| `agency_name`, `citizen_label` | Full agency name and catalog problem description. |
| `needs_review` | `true` whenever `status != ASSIGNED`. |
| `rules_applied` | IDs of the rules evaluated for this request. |
| `findings` | One entry per observation: `rule`, `code`, `severity`, `message`, `field`. |
| `referral_advisory` | R14 historical referral context, or `null`. It never changes the agency. |

## 6. Business rules

| ID | Rule |
|---|---|
| R01 | Validate the CO1 input (fields, types, confidence range, code format). |
| R02 | Emergency restriction: only `CONTINUE` proceeds; `STOP_CALL_911` blocks; `CONFIRM_WITH_CITIZEN` holds; contradictions hold. |
| R03 | `ready_to_submit = false` holds. |
| R04 | `needs_human = true` holds. |
| R05 | `EMERGENCY` blocks; `OTHER` and `SMALLTALK` are held and follow the intake workflow. |
| R06 | Exact catalog lookup. |
| R07 | Unknown code: UNRESOLVED, with no invented agency. |
| R08 | Conflicting, duplicate or incomplete catalog entries are never resolved arbitrarily. |
| R09 | Confidence passed through unchanged; optional, configurable review threshold. |
| R10 | Skills only from the catalog; missing skill means UNRESOLVED. |
| R11 | Explanation naming every blocking rule; full rule list in the detail block. |
| R12 | Safe unresolved state: no partial assignment. |
| R13 | Downstream eligibility requires every gate. |
| R14 | Historical referral advisory: informational unless configured. |

Run `civicops-agency rules` for the full statements.

## 7. Package layout

```text
civicops_agency/        runtime package (stdlib only)
  router.py             AgencySkillRouter (public API)
  rules.py              business rules R02–R14, rule register, status precedence
  validators.py         R01 CO1 validation
  catalog.py            catalog loader and integrity checks (R08), agency names
  knowledge.py          optional referral knowledge (R14)
  explanations.py       R11 explanation builder
  schemas.py            input/output dataclasses and vocabularies
  config.py             RouterConfig
  simulator.py          CO1 simulator (structured messages, no NLP)
  cli.py, __main__.py   command-line interface
data/reference/         request_catalog.csv, NYC311_agency_matrix.md, referral_knowledge.json
data/research/          requests_real.parquet (research package only)
docs/                   shared data reference (CO1 contract)
research/               offline knowledge discovery and method selection
benchmarks/             approach comparison and performance
examples/               inputs/ (CO1 messages), outputs/ (router results), demo.py
tests/                  pytest suite (297 tests)
outputs/                research tables, benchmark results, test results
reports/                agency_skills_report.html and its builder
DECISIONS.md            decision register
run_all.py              reproduce everything
run_demo.py             one-command demo
make_dist.py            build the runtime and research ZIPs
```

## 8. Reproducing the research, tests, benchmark and report

The research scripts need the historical dataset in
`data/research/requests_real.parquet`, or a path in `$CIVICOPS_HISTORY`. First
install the research dependencies:

```bash
python -m pip install -r requirements.txt
```

Run everything (research, tests, benchmark and report):

```bash
python run_all.py
```

Without the dataset, run tests, benchmark and report only:

```bash
python run_all.py --skip-research
```

Steps:
1. `research/catalog_consistency.py` checks catalog integrity and compares it with the matrix.
2. `research/historical_analysis.py` covers splits, coverage, agency distributions, disagreements, patterns, temporal stability and missing values.
3. `research/referral_analysis.py` covers referral semantics, destinations, rates and the matrix explanation.
4. `research/method_comparison.py` evaluates candidates A, C1–C3 and the referral-risk estimators, and writes `data/reference/referral_knowledge.json`.
5. `pytest` writes `outputs/tests/junit.xml`.
6. `benchmarks/benchmark_agency.py` writes `outputs/benchmarks/benchmark_results.json`.
7. `reports/build_report.py` writes `reports/agency_skills_report.html` from the files above.

## 9. Distribution packages

| Package | Contents | For |
|---|---|---|
| `civicops_agency_runtime-1.1.0.zip` | source, catalog, matrix, knowledge JSON, tests, examples, docs, report, research scripts and their result tables. **No historical dataset.** | team members integrating the router |
| `civicops_agency_research-1.1.0.zip` | the runtime package plus `data/research/requests_real.parquet` (≈5 MB, processed NYC 311 open data) | reproducing the research |

In the runtime package, the dataset-dependent history test is skipped
automatically, and `run_all.py` reuses the last historical replay figures
(the report says so). To reproduce the research from it, copy
`requests_real.parquet` into `data/research/` or set `CIVICOPS_HISTORY`.

**Windows note:** `pip install .` builds the package in a nested temporary
folder. If you extract the ZIP under a very long path, the build can exceed
Windows' 260-character path limit and fail with "No such file or directory".
Extract to a short path such as `C:\work`, or enable long paths in Windows.

## 10. Downstream integration

The next component is **duplicate detection** (shared pipeline: intake bot >
emergency check > agency and skill routing > duplicate detection > ... > crew
deployment). It receives the full output: the CO1 fields it already knows, plus
`agency`, `required_skills`, `status`, `dispatch_eligible`, `next_step` and
`explanation`. Requests that are not `ASSIGNED` carry a `next_step` other than
`DUPLICATE_DETECTION`, saying where they go instead.

At the end of the pipeline, crew deployment uses the added fields:

```python
if payload["dispatch_eligible"]:
    crews = [c for c in crew_table
             if c["agency"] == payload["agency"]
             and set(payload["required_skills"]) <= set(c["skills"].split("|"))]
    # then: prioritisation order, geocoding payload["slots"], capacity, routing (other components)
```

Service time is **not** passed on. Crew deployment reads `est_service_minutes`
from the catalog with the same RT code.
