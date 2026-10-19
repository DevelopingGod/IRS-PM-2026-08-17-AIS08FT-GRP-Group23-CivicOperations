# C10 — Urgency Assignment

NUS-ISS IRS coursework research prototype. Not official NYC 311 policy or an approved dispatch decision system.

## Current implementation

**Entry point:** `c10_urgency_v11_stage6_1.py` (`assess(request, root=...)`). This is the consolidated Stage 6.1 implementation used by C11. Do **not** import the archived standalone `c10_urgency.py` from older versions.

- Uses `request_type_code` and `request_catalog.csv` for a baseline.
- Holds requests unless emergency action is `CONTINUE` (legacy `cleared_non_emergency` is also accepted); `STOP_CALL_911` and `CONFIRM_WITH_CITIZEN` are held.
- Uses contextual IF–THEN inference to consider hazards, exposure, and timing. Inferred evidence is not verified evidence.
- Does not reduce urgency because of low credibility or suspected fake reports; evidence-gated CRITICAL requires human review.
- Emits uppercase urgency (`CRITICAL`, `HIGH`, `MEDIUM`, `LOW`), `recommended_urgency`, `c11_urgency`, and `c11_queue_status` (`provisional`, `provisional_review`, `hold`). A queue position does not approve dispatch.

## Files

| File | Purpose |
|---|---|
| `c10_urgency_v11_stage6_1.py` | Main assessment function; C11 integration entry point |
| `c10_catalog_mapping.py` | Catalog lookup and baseline urgency |
| `c10_reasoning.py` | Transparent contextual inference |
| `request_catalog.csv` | RT-code catalog |
| `demo_scenarios.csv` | 34 demonstration inputs |
| `reasoning_escalation_demo.csv` | Illustrative reasoning scenarios |
| `test_stage6_1.py` | Core integration and safety regression checks |
| `test_consolidated_v11.py`, `test_reasoning_stage3.py` | Earlier development checks; run if applicable |

## Run

Requires Python 3.11+; standard library only. From this folder:

```powershell
python test_stage6_1.py
```

From a sibling C11 folder, C11 must explicitly import `c10_urgency_v11_stage6_1.assess` from this folder and pass this directory as `root` so the catalog resolves correctly. Keep `C10_Urgency_Assignment` and `C11_Request_Prioritization` as siblings.

## Validation and limitations

The Stage 6.1 tests cover 34 demonstration contracts, evidence-gated CRITICAL, emergency safety, and credibility/fake non-downgrade. They are behavioral checks, **not** measured real-world classification accuracy. Human oversight remains required for flagged cases.

The demonstration data may not include rich enough descriptions to show urgency changing; use the reasoning scenarios to explain this limitation, and do not claim a demonstrated escalation unless tested against the actual RT-code baseline.
