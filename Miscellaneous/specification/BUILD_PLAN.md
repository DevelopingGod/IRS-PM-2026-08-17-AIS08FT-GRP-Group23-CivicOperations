# Civic Operations: Build Plan v2 (post-proposal)

Status: APPROVED BASELINE, 18 Sep 2026. The submitted proposal (12 Sep) is frozen; this plan is how we deliver it.
The readable version for the team is `05_Team_Workspace/01_Start_Here/Civic-Operations-Team-Build-Guide.pdf`. This file is the short engineering record.

## Decisions (Sankalp, 18 Sep)
| Topic | Decision |
|---|---|
| Frontend | Next.js on Vercel, 3 portals. No Streamlit |
| Backend / DB | FastAPI on Hugging Face Spaces (Docker, free CPU); Supabase (Postgres + PostGIS + Auth + Realtime) |
| Areas | The 5 NYC boroughs, about 10k real rows each (49,558 after catalog mapping) |
| New component | C04 Agency & Skill Routing, owned by Mohammad |
| Ownership | As in the proposal, plus C04. C14 Admin Q&A has no owner yet (team decides). No reassignment suggested |
| Contracts | Locked v1.0.0 in `packages/contracts`; patch/minor/major change process |
| Placeholders | Teammates' components are thin stubs (safe default + TODO); acceptance tests switch on when VERSION is bumped |
| 28 Sep prototype | Clickable UI mockups (`05_Team_Workspace/11_Prototype_Mockups/index.html`); each member presents their own components |
| Notebooks | `03_Research/notebooks/01` (data) and `03_Research/notebooks/02` (NLP) are READ FIRST for everyone; Mohammad runs 02 in full on GPU |
| GitHub | Push to main |

## Pipeline (strict DAG)
C02+C03 gate (STOP means 911, nothing stored) → C04 → C05 → C08 → C07 → C06 → C09 → C10 → C11 (queue).
Batch: C12 every 15 min or "Plan now"; C13 hourly. Closure: crew outcome → C07 verification → credibility of every reporter in the group.
The orchestrator (`services/api/app/pipeline.py`) is the only writer. It enforces the state machine and the safe default for each component.

## What exists now
- `packages/contracts`: 26 example payloads, JSON schemas, 65 tests
- `services/api`: orchestrator, API, C01/C03/C07/C13 baselines (Sankalp), thin placeholders for everyone else. 10 skeleton tests pass; 32 acceptance tests are waiting on owners
- `data/pipeline`: download (resumable), clean + real labels, aggregates, the shared synthetic population, NLP data. Committed sample: `data/sample` (2.7 MB)
- `03_Research/notebooks/01`, `03_Research/notebooks/02` (smoke-run on CPU: intent gold macro-F1 0.72 keywords → 0.88 TF-IDF → 0.93 MiniLM; emergency recall 0.68 → 0.89)
- Documents in `05_Team_Workspace/` (NUS-ISS theme): Team Build Guide, Component Specification & Contracts, Examiner Q&A, 10-slide deck
- `06_Workspace_Tools/deck_assets/chart_architecture_v2.png`

## Timeline
19 to 22 Sep read and comment · 28 Sep presentation · 3/6/9 Oct integration checkpoints · 15 Oct freeze · 25 Oct submit.

## Review record (/autoplan, 18 Sep 2026)
Run in a shortened form: one independent CEO/strategy reviewer and one combined engineering + design + DX reviewer. Both were Claude
subagents, because the Codex CLI is not installed, so there was no outside-model coverage.

**Strategy (CEO) findings and what we did**
| Finding | Severity | Resolution |
|---|---|---|
| The proposal's admin NL Q&A (Cognitive Systems) had been silently dropped | Critical | Restored as C14; owner to be agreed |
| Integration-collapse risk; Mohammad overloaded | Critical | Pipeline runs end to end on placeholders; 3 checkpoints; load raised as a team question (no reassignment, per Sankalp) |
| Make replay + ablation the headline experiment | High | Adopted (Guide §9, deck slide 9) |
| Synthetic-label circularity | High | Real proxy labels, hand-written gold set, independent urgency rubric, honesty-vs-outcome check r = 0.89 |
| Free host cold starts; 2 s p95 assumption | High | Warm-up plus local fallback; latency is measured by the tests |
| C03 must run on ALL text, not only the bot | High | Form free text and bot turns are screened; crew-note screening (C02) is a TODO for the C02 owner |
| Leaderboard ethics | Medium | Opt-in, pseudonyms, verified reports only |
| Contracts v0.x until integration | Medium | Kept LOCKED (user direction); mitigated with patch/minor/major rules |
| LLM arm in the NLP benchmark | Medium | Listed as future work / an optional notebook arm |

**Engineering / design / DX findings and what we did**
| Finding | Resolution |
|---|---|
| No single writer; no state machine | Orchestrator is the only writer; `LEGAL_TRANSITIONS` plus a test for illegal moves |
| Idempotency (submit, crew outcome) | client_submission_id and assignment_id; tested |
| C06 failure could deprioritise a citizen | Safe default PROCEED; test `test_failed_component_uses_safe_default` |
| Recurrence vs duplicate | Explicit in the C05 contract (`recurrence_of`) |
| No-access outcome had no path | ON_SITE → PROCESSED on NO_ACCESS |
| Rate limiting, API key, RLS | Rate limit + shared-secret header in the API; RLS is a task for the infra owner (T24) |
| Developer experience: time to hello-world | Stubs, sample data, contract and acceptance tests; Build Guide §8 |
| Offline crew outcomes, compare-and-set dispatch, advisory lock | Contract supports them (row_version, assignment_id); implementation is with the C12 and frontend owners |

Open items are in `05_Team_Workspace/06_Tracker/Tracker.xlsx` → Open Questions (11) and Task Board (T01 to T31).
