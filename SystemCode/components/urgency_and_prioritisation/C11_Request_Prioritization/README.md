# C11 — Request Prioritization

**NYC 311 Civic Operations | NUS-ISS Intelligent Reasoning Systems (IRS) project**  
**Status:** Research prototype / team-review candidate (15 October 2026). **Not** an official NYC 311 prioritization policy, dispatch authorization, or validated operational system.

## Purpose

C11 builds explainable **municipality-wide and agency-specific service queues** from non-emergency 311 requests. It obtains provisional urgency from the shared **C10 Urgency Assignment** component, looks up published SLA reference information when an exact verified category match exists, and ranks requests using waiting time, SLA risk, corroboration, approved escalation, sensitive-site impact, and aging rules. C11 **does not change C10 urgency**.

## Current capabilities

- **Shared C10 integration:** calls `assess()` from `../C10_Urgency_Assignment/c10_urgency_v11_stage6_1.py` and does not trust caller-supplied urgency.
- **Emergency gate:** C10 `hold` decisions are excluded from active queue positions. A C05 `DUPLICATE`/non-primary report is likewise held to avoid a separate crew task.
- **Provisional review (Option A):** `provisional_review` requests remain ranked and visibly flagged. **Every C11 result has `dispatch_approved=False`; a queue position never authorizes dispatch.**
- **Uppercase urgency contract:** `CRITICAL`, `HIGH`, `MEDIUM`, `LOW`.
- **C05 corroboration:** `corroboration_count` represents **distinct reporters**, not total submissions. When primary and related reports arrive together, the highest incident-group count is propagated to the primary. Cross-batch aggregation requires an upstream incident store.
- **Provisional C07 escalation adapter:** supports `NONE`, `SUPERVISOR`, `CENTRAL_AUTHORITY`; escalation points require `escalation_approved=True`. C07's final approval semantics still need confirmation. Legacy `approved_escalation` is supported for backward compatibility when no escalation level is supplied.
- **SLA matching:** exact-match lookup using official agency, problem, problem details, and additional details, with a reviewed RT-code mapping. Optional `verified_official_category` enrichment requires `verified: true` and a source; no SLA is guessed from RT code alone.
- **Starvation protection:** a matched overdue SLA or an unmatched request waiting at least **168 hours (7 days)** receives a minimum scheduling band of `NEXT`. The 168-hour threshold is experimental.
- **Explainability:** records score components, rule/band reasons, SLA status, review/hold information, and queue positions.

## Repository layout and dependencies

Keep these as **sibling directories** at the repository level:

```text
IRS-Project-2026-KIMS/
├── C10_Urgency_Assignment/
│   ├── c10_urgency_v11_stage6_1.py
│   ├── c10_catalog_mapping.py
│   ├── c10_reasoning.py
│   └── request_catalog.csv
└── C11_Request_Prioritization/
    ├── c11_final.py                  # Primary integrated entry point
    ├── c11_prioritization.py         # Scoring and input validation
    ├── c11_sla_lookup.py             # Exact-detail SLA lookup
    ├── c11_category_enrichment.py    # Verified category adapter
    ├── nyc311_official_sla.csv
    ├── c11_water_sewer_SLA_detail_review.csv
    ├── demo_scenarios.csv
    ├── test_c11_final.py
    ├── test_c11_enhancements.py
    ├── test_c11_starvation.py
    ├── test_c11_comment3.py
    ├── test_c11_comment4.py
    └── README.md
```

**Python:** 3.11+ (standard-library C10/C11 paths shown here; other team components may have additional dependencies). C11 does **not** require copies of C10 Python modules inside its own directory. Do not upload backup copies, `__pycache__`, or nested extracted release folders as production files. The sibling import has passed **local** tests; execution in the final GitHub layout remains to be verified.

## Entry point and example

Call `prioritize_from_c10(requests, now, root=...)` in `c11_final.py`. `requests` is a list of dictionaries and `now` is a timezone-aware ISO-8601 timestamp.

From the **C11 folder** in PowerShell:

```powershell
python test_c11_final.py
python test_c11_enhancements.py
python test_c11_starvation.py
python test_c11_comment3.py
python test_c11_comment4.py
```

Programmatic example (from within the C11 folder):

```python
from c11_final import prioritize_from_c10

requests = [{
    "request_id": "DEMO-001",
    "request_type_code": "RT002",
    "agency": "DOT",
    "emergency_action": "CONTINUE",
    "created_date": "2026-10-12T12:00:00+00:00",
}]
queue = prioritize_from_c10(requests, "2026-10-15T12:00:00+00:00")
print(queue[0]["c11_queue_status"], queue[0]["priority_band"])
```

The example illustrates the interface only; the actual urgency and SLA depend on the catalog and verified inputs. `prioritize_from_c10()` is the integrated entry point; the lower-level `prioritize()` in `c11_prioritization.py` does not perform the same C10/SLA integration and should not be presented as the complete workflow.

## Inputs and component contracts

| Input | Source / interpretation |
|---|---|
| `request_id` (or `scenario_id`), `request_type_code`, `agency` | Intake and agency routing; required for a ranked record |
| `emergency_action` | Upstream emergency screen: `CONTINUE` permits C10 processing; other/missing clearance actions hold |
| `created_date` | Request timestamp; **currently must include timezone** (e.g. `+00:00`) |
| `relation`, `is_primary`, `duplicate_of`, `duplicate_group_id`, `corroboration_count` | C05 duplicate/corroboration contract; only primary incident is queued |
| `escalation_level`, `escalation_approved` | Provisional C07 contract; level alone is not approval |
| `verified_sensitive_site_impact` | Verified contextual input for sensitive-site scoring |
| `verified_official_category` | Optional dict with `verified`, `source`, `official_agency`, `official_problem`, `official_problem_details`, optional `official_additional_details` |
| `official_agency`, `official_problem`, `official_problem_details`, `official_additional_details` | Legacy direct official category fields for exact SLA lookup |

C11 recomputes C10 urgency and discards caller-supplied C10 output fields and `sla_target_hours`. Missing/unverified SLA category information produces `SLA_UNKNOWN`, not an invented deadline. Some inputs and approvals are assumed to be verified by upstream components; C11 does not independently authenticate their provenance.

## Scoring and scheduling (current implementation)

**Current experimental scoring** in `c11_prioritization.py`:

| Factor | Maximum points / rule |
|---|---:|
| Waiting time | 25 (saturates at 48 hours) |
| SLA utilization | 35 (only when a matching SLA and waiting time are available) |
| Approved escalation | 25 (scaled by escalation level; legacy approved escalation supported) |
| Verified sensitive-site impact | 15 |
| C05 distinct-reporter corroboration | **Additional** 0–10 bonus (1→0; 2→2.5; 3–4→5; 5–9→7.5; 10+→10) |
| Aging `AG01` | +10 at ≥48 hours |
| Aging `AG02` | +20 at ≥96 hours (replaces, does not stack with, AG01) |

**Important:** The score can exceed 100 because corroboration and aging are additional bonuses. These are experimental design assumptions and **do not yet match the Slide 7 weights** (urgency 0.60, waiting 0.15, corroboration 0.10, context 0.10, escalation 0.05). The team must reconcile implementation and slide under Comment 5.

Scheduling uses **band first**, then urgency, descending score, older creation time, and request ID. Default bands: `CRITICAL → ACT NOW`, `HIGH → NEXT`, `MEDIUM → PLANNED`, `LOW → MONITOR`. Overdue matched SLA or unmatched waiting ≥168 hours raises the band to **at least NEXT** without changing urgency. Held requests have `HOLD` and no queue position.

## Outputs

Selected output fields:

- `urgency_level`, `c11_urgency`, `recommended_urgency` — uppercase C10-derived urgency.
- `c11_queue_status` — `provisional`, `provisional_review`, or `hold`.
- `priority_score`, `base_priority_score`, `priority_reasons`, `waiting_hours`, `aging_rule`, `aging_bonus`.
- `priority_band` — `ACT NOW`, `NEXT`, `PLANNED`, `MONITOR`, or `HOLD`.
- `overall_queue_position` — 1-based overall scheduling order; `None` for held requests.
- `queue_position` — urgency-prefixed rank (`C1`, `H1`, `M1`, `L1`, etc.); `agency_position` — corresponding agency-specific urgency rank.
- `sla_status`, `sla_category_status`, `sla_category_reason`, `sla_target_hours`, `sla_deadline`, `sla_utilization_pct`, `sla_overdue`, `sla_reason`.
- `starvation_protection_applied`, `starvation_rule`, `starvation_reason`.
- `dispatch_approved=False`, `dispatch_authorization='not_authorized_by_C11'`.

`queue_position` is an **urgency-group identifier**, not the same as `overall_queue_position` after band-first sorting.

## Test evidence (local, user-reported)

The latest local PowerShell run reported:

- `test_c11_final.py`: 34/34 C10→C11 demonstration contracts, emergency holds, untrusted urgency/SLA checks, aging, and exact-detail SLA test **PASS**.
- `test_c11_enhancements.py`: **6 tests OK**.
- `test_c11_starvation.py`: old LOW ahead of fresh MEDIUM via NEXT floor, emergency holds, overdue SLA floor **PASS**.
- `test_c11_comment3.py`: **5 tests OK** (corroboration, duplicate holds, escalation approval, invalid contracts).
- `test_c11_comment4.py`: **4 tests OK** (verified enrichment and non-guessing safeguards).

These are controlled functional tests, **not** measured ranking accuracy or production validation. The 34 original demo scenarios still lack verified official category fields, so their SLA status remains `SLA_UNKNOWN` by design.

## Known limitations and remaining team feedback

1. **GitHub execution:** sibling C10 integration passes locally; verify against actual repository layout and imports on a fresh checkout.
2. **Starvation:** implemented and locally tested, but experimental thresholds require team sign-off.
3. **C05/C07 integration:** synthetic tests pass; actual upstream data wiring, incident-group refresh, and C07 approval semantics require verification.
4. **SLA coverage:** verified category enrichment works, but **0/34 original demo cases** have sufficient upstream official fields; no coverage improvement should be claimed yet.
5. **Slide 7 weights:** scoring formula still differs; team alignment required.
6. **New York timestamps:** naive NYC-local datetimes are **not yet supported**; the parser currently requires timezone-aware timestamps. Daylight-saving handling remains to be implemented.
7. **Ranking evaluation:** NDCG, Kendall tau, and starvation metrics against the 100 synthetic expert-ranked snapshots are **not yet implemented**. Synthetic rankings must be labeled as sanity checks.

## Safety and provenance

This component produces **advisory rankings only**. Human reviewers remain responsible for operational decisions. C10 handles emergency clearance; C11 must not dispatch held requests. The SLA reference files require provenance review before use as official commitments. Neither scores nor priority bands are approved NYC 311 policy.
