# C12 Admin Q&A (formerly C14) - results

Owner: Nabeel Taresh. Status: local branch `nabeel_c12`, not pushed.

## Purpose
An admin asks a question in plain English about the request queue. C12 selects the rows, computes the answer, and writes one short sentence plus a table. It only reads. It never shows SQL, personal data, or a per-citizen credibility score.

## Contract (agreed with Sankalp)
- Input: `run(AdminQuestion(question 3-300 chars, admin_id), rows, history=None, strategy=None, now=None)`.
- `rows` is the FULL read-only admin snapshot, not the answer rows. Required columns: request_id, label, request_type, agency, borough, urgency_level, queue_position, status, created_at, crew, credibility_score, eta_low_hours, eta_high_hours. Optional: closed_at, resolution_status, complaint_family, priority_band, priority_reasons.
- `history` is optional cleaned 311 history. C12 uses it for TREND and for resolution-time AVERAGE when it has every filter column.
- Output: `AdminAnswer(meta, matched_intent, filters, answer_text, table <= 200 rows, confidence 0-1, suggestions)`.
- Safe default: UNKNOWN, empty table, five example questions.
- Intents: COUNT, TOP_N, AVERAGE, TREND, LIST, WHY_RANKED, UNKNOWN.
- No SQL. C12 follows the old C14 rule "never generate SQL": fixed pandas code runs a validated query plan. (A `guarded_sql` strategy was built and then removed on 2026-10-15; see CHANGELOG.)

## How it works
1. Safety screen (all strategies). Regular expressions block writes, schema questions, prompt injection, personal data, and per-citizen credibility. Result: UNKNOWN.
2. Slot extraction (shared). Borough, request type or family, status (open, closed, or one status), agency, urgency, crew, priority band, dates ("last week", "in March", "last month", "this month", "last N days", "since <month>"), group_by, top n, metric, period, request id. Dates are relative to the newest created_at in the snapshot.
3. Intent. This is the step where the strategies differ (see below).
4. Execution. Fixed pandas code runs the plan.
5. Formatting. One template per intent. Every number in the text comes from the computed table.

| Strategy | Intent | Execution | Notes |
|---|---|---|---|
| `rules` | ordered regular expressions | pandas filters and groupby | fast, transparent |
| `ml_intent` | TF-IDF (word + char) + logistic regression, trained on 408 templated phrases (seed 2026) | pandas | no keyword list for intent |
| `hybrid` (default) | rules; the classifier fills in when no rule fires | validated JSON plan, then fixed pandas code | the plan has fixed keys and vocabulary |

LLM: no local or free LLM is available on the box without a paid key (no Ollama, no local model). So `hybrid` fills its JSON plan from rules and the classifier. The plan validator is the place where an LLM plan would be checked in future. This is a design, not a tested LLM path.


### Empty results and follow-ups
- If the filters match 0 rows, C12 checks the filters one at a time (type, family, borough, agency, urgency, crew, band, status, dates). If a value is not in the data at all, it says "This data has no <value> requests". Otherwise it names the first filter that makes the result empty: "There are noise requests in Brooklyn, but none are open, so the count is 0." It lists up to 3 values that do exist, and lowers the confidence by 20%.
- Every answer has 3 follow-up questions on the same filters (count -> top types, trend, average ETA; top -> count, trend, ETA; average -> count, average by group, trend; trend -> count, top, monthly trend; list -> count, top, why ranked; why ranked -> open list, top types, high-urgency count). After an empty result, the first follow-up is the same question with a value that exists. Each follow-up is tested to parse to a known intent.

## Benchmark
- 74 gold questions, written first, before any strategy ran: 62 in scope, 12 out of scope, unsafe, or injection (16%).
- Gold plans are hand-written. Gold answers come from a separate simple pandas function in `tools/c12_admin_qa_eval.py`, not from the component.
- Frozen snapshot: 2,159 rows from real NYC 311 (1,759 open, 400 closed), plus 1,200 history rows. See DATA_REGISTER.
- Metrics: intent accuracy; slot F1 (micro, over filter key/value pairs); row-selection precision and recall on request_id (mean per question, in scope); exact answer; grounding (every number in the text is in the table, the filters, or the row count); safety (out-of-scope, unsafe, injection get UNKNOWN and an empty table); output safety (in scope: no SQL-like text, no per-row credibility, at most 200 rows); p50 and p95 latency (warm, MacBook Air).
- 95% CIs: bootstrap over questions, 1,000 resamples, seed 2026.

| Strategy | Intent acc | Slot F1 | Row P | Row R | Exact | Grounding | Safety | p50 / p95 ms |
|---|---|---|---|---|---|---|---|---|
| rules | 0.959 (0.91-1.00) | 0.957 (0.90-1.00) | 0.968 (0.92-1.00) | 0.968 (0.92-1.00) | 0.959 (0.91-1.00) | 1.00 | 1.00 (12/12) | 3.6 / 4.9 |
| ml_intent | 0.932 (0.88-0.99) | 0.941 (0.88-0.99) | 0.935 (0.88-0.99) | 0.935 (0.87-0.98) | 0.932 (0.86-0.97) | 1.00 | 1.00 (12/12) | 4.0 / 5.3 |
| hybrid | 0.959 (0.91-1.00) | 0.957 (0.90-1.00) | 0.968 (0.92-1.00) | 0.968 (0.92-1.00) | 0.959 (0.91-1.00) | 1.00 | 1.00 (12/12) | 3.6 / 4.7 |

Output safety on the 62 in-scope questions is 1.00 for every strategy. Exact answer by intent (hybrid): AVERAGE 1.00, COUNT 0.93, LIST 0.91, TOP_N 0.91, TREND 1.00, UNKNOWN 1.00, WHY_RANKED 0.83.

Misses (hybrid and rules; Q18 "Top 3 agencies ..." fixed in v1.1): Q59 "What puts NYC-... ahead of the others?" (no "why", read as LIST), Q60 and Q62 (paraphrases with no keyword: "Tally of ... tickets", "tickets ... that are unresolved"). ml_intent also misses Q23 and Q61, but gets Q59.

## v1.1.0 (2026-10-15): write refusals, unknown words, dates, crews
Fixes made after a 69-question blind test. **The blind set has now been seen**, so its "after" score is no longer a fully blind estimate. The fixes are general (no blind wording was copied), and the new unit tests use different wording.

| Strategy | Gold exact before | Gold exact after | Blind exact before | Blind exact after | Blind lenient after |
|---|---|---|---|---|---|
| rules | 0.946 (0.89-0.99) | 0.959 (0.91-1.00) | 31/69 = 0.449 (0.338-0.566) | 47/69 = 0.681 (0.564-0.779) | 0.754 (0.640-0.840) |
| ml_intent | 0.932 (0.86-0.97) | 0.932 (0.86-0.97) | 31/69 = 0.449 (0.338-0.566) | 44/69 = 0.638 (0.520-0.741) | 0.710 (0.594-0.804) |
| hybrid | 0.946 (0.89-0.99) | 0.959 (0.91-1.00) | 31/69 = 0.449 (0.338-0.566) | 47/69 = 0.681 (0.564-0.779) | 0.754 (0.640-0.840) |

Gold CIs: bootstrap (1,000 resamples). Blind CIs: Wilson 95%. Gold safety stays 12/12. Blind unsafe refused: 8/8 for every strategy (was 7/8 for rules and hybrid). Blind out-of-scope declined: 5/5 (was 4/5). Gold p50 went from about 3.7 ms to about 8.3 ms, because of the vocabulary check.

Remaining blind misses (hybrid, 22):
- unsupported question types: comparisons, first-N, oldest (Q39-Q45, Q17, Q49);
- ETA low/high bounds and upper-ETA metric (Q27, Q28, Q31);
- TOP_N from history, and TREND using the snapshot (Q26, Q37);
- parsing (Q29, Q34, Q38, Q50, Q53);
- unsupported filters such as crew not-null and numeric thresholds (Q52, Q56);
- grader strictness (Q22).

## Pros and cons
- rules: best here and easiest to read. It fails on wording it has no pattern for.
- ml_intent: finds some intents without keywords (Q59). It is a little worse overall, because 408 templated training phrases are a small and narrow set.
- guarded_sql (removed): it gave the same answers as rules, because both used the same plan, with a higher p95 (6.5 ms). It added code and a second execution path for no accuracy gain, and it broke the "never generate SQL" contract.
- hybrid: the same as rules on this set. The classifier fallback did not rescue Q60 or Q62 (confidence under 0.6). It is the default because the validated plan is the safest place to add an LLM later.

## Lessons
- Row selection is the hard part. Every miss is a selection miss (intent or slot), not a formatting miss. Formatting was exact whenever the plan was right.
- The three strategies differ only in the intent step. They share slot extraction, so their slot F1 is almost the same.
- An LLM is not needed for these 7 intents on this vocabulary. Paraphrases ("tally", "tickets") are the gap, and an LLM or a larger classifier would help there.
- An eval bug gave a false 0.00 safety score at first: the leak check matched the word "snapshot" in the refusal text. The check now looks for SQL shapes only.
- Before its removal, the benchmark found a bug in `guarded_sql` (TREND request ids came from the wrong table). Two execution paths double the places for such bugs.

## Limits
- Follow-ups are templates. They do not check that the follow-up result is non-empty, except the retry question after an empty result. The gold set has only 1 empty-result question, so the reason text is checked mainly by unit tests.

- The same person wrote the rules, the classifier training phrases, and the gold questions. The scores are optimistic. A teammate should write a second, blind question set.
- 74 questions. The CIs are wide (about ±5 points).
- The snapshot is real 311 rows, but crew, credibility_score, queue_position, priority_band, and priority_reasons are synthetic, and the ETA range is derived. Answers about those columns test the mechanics, not real values.
- The safety screen is a list of regular expressions. It blocked all 12 test attacks, but a new wording can pass it. The second layer is the plan validator (fixed keys and values); there is no query language to inject into.
- English only. No spelling correction.
