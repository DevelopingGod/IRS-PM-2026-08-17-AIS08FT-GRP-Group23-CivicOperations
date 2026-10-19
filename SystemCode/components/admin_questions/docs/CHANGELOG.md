# C12 Admin Q&A - change log

## 2026-10-15 (session 1) - First build, local only
- Code: added components/c12_admin_qa.py (one file, run(...) entry point) and registered it as `components.admin_qa`. Added it to health_check().
- Strategies: rules, ml_intent, guarded_sql, hybrid (default). Same slot extraction and formatting for all.
- Decision: no local or free LLM is available, so guarded_sql uses template NL->SQL and hybrid uses a template-filled JSON plan. This is stated in RESULTS.md.
- Decision: the new contract allows internal SQL (one strategy). This changes the old C14 text "never generate SQL". SQL never leaves the component. Needs Sankalp's agreement.
- Decision: credibility_score is shown only as an average over 5 or more requests. Per-row and per-citizen credibility questions get UNKNOWN.
- Data: frozen snapshot and history sample from real NYC 311 rows, some columns synthetic (see DATA_REGISTER.md).
- Benchmark: 74 gold questions written first (12 out-of-scope, unsafe, or injection). Hybrid: intent 0.959, exact 0.946, safety 12/12, p95 4.9 ms. Full table in RESULTS.md.
- Fixes found by the benchmark: (1) eval leak check matched "snapshot" in refusal text (eval bug); (2) guarded_sql took TREND ids from the wrong table (component bug). No pattern was changed to fit a gold question.
- Tests: components/tests/test_c12_admin_qa.py, 21 tests pass.
- Branch nabeel_c12 from origin/sankalp_code (8c686c1), worktree /tmp/irs-c12. Not pushed.

## 2026-10-15 (session 2) - guarded_sql removed
- Decision (Nabeel): remove the `guarded_sql` strategy, the SQL validator, and all SQLite use. Reason: follow the contract ("never generate SQL") and keep C12 simple. It gave the same answers as `rules` (exact 0.946) with a higher p95 (6.5 ms vs 4.9 ms).
- Strategies now: rules, ml_intent, hybrid (default). Removed 1 SQL test and the guarded_sql case of the per-strategy test. Kept the "no SQL in the output" test.
- Benchmark rerun: rules and hybrid exact 0.946, ml_intent 0.932, safety 12/12 for all. Unchanged except for the removed row.
- Tests: 19 pass. Not pushed.

## 2026-10-15 (session 3) - Empty-result reasons and follow-up questions
- Empty results: when the filters match 0 rows, answer_text says why. If a filter value is not in the data: "This data has no noise requests, so the count is 0." If each value exists but the combination is empty, it names the filter that empties it: "There are noise requests in Brooklyn, but none are open, so the count is 0." It then lists up to 3 values that do exist for that filter. Confidence is multiplied by 0.8 (EMPTY_CONFIDENCE_FACTOR).
- Follow-ups: every answered intent returns 3 follow-up questions on the same filters (for example after a count: top types or top borough, weekly trend, average ETA; after an empty result: the same question with a value that exists first). Every follow-up is a question C12 can answer (tested). UNKNOWN still returns the example questions.
- Benchmark rerun: no score changed (hybrid intent 0.959, exact 0.946, safety 12/12, p95 4.8 ms). 1 of 74 gold questions goes through the empty-result path; all 60 answered questions get 3 follow-ups.
- Tests: 8 new (empty reason x2, follow-ups x6 intents). 27 pass. Not pushed.

## 2026-10-15 (session 4) - Merged origin/nabeel_c05_c06 into nabeel_c12
- Merge commit (no rebase) of origin/nabeel_c05_c06 (949ea00, C05 duplicate + C06 fake risk) into nabeel_c12.
- One conflict, components/__init__.py: kept both sides. Imports and __all__ now register duplicate, fake_risk and admin_qa; docstring describes all three; health_check reports C05, C06 and C12.
- Tests: components/tests 55 pass (C05/C06 28 + C12 27). Benchmark rerun unchanged: hybrid intent 0.959, exact 0.946, safety 1.00.
- Not pushed.

## 2026-10-15 (session 5) - v1.1.0: blind-test fixes (write refusals, unknown words, dates, crews)
- Safety: any write verb aimed at a record or field is refused. Strong verbs (change, update, edit, delete, remove, reassign, escalate, ...) need only a target. Weak verbs (set, assign, close, mark, move, bump) need "to/as", or must start the sentence. Fields covered include urgency, status, crew, priority, band and ETA. Read questions such as "how many did we close" still answer.
- Unknown words: the new `check_terms` looks at entity slots: the word before complaints/requests/tickets, the word after "borough of", capitalised words, crew IDs, and lower-case typos. It checks them against the snapshot vocabulary plus the parser's own words. A close match (difflib >= 0.8) is mapped, and the answer says so ("Note: I read 'Isand' as ISLAND"). Anything else gets a low-confidence (0.3) answer with 0 rows that names the term. A filter is never dropped to give a whole-table count.
- Synonyms: lite/lamp, heating/radiator, waiting for / awaiting dispatch / unassigned (= SUBMITTED + PROCESSED), busiest/most active (top-N cue and group), resolution_status (unfounded, duplicate, genuine, subsided, no access, administrative), group "reasons/outcomes", "top N <group>".
- Dates (relative to the snapshot): day ranges (Oct 1-7, Oct 1 – 7 2026, Jan 1 and Mar 31), since/after/from <month day>, on <month day>, between/from <month> and <month>, last year, this year, from/in/during <year>, year ranges, <month> <year>, and ISO ranges. Month abbreviations are accepted. An empty date filter now states the data's date span.
- Crew IDs are read anywhere (FORESTRY-02, road_repair-01). "dispatched to <crew>" filters on the crew, not on status.
- Results: gold exact, hybrid and rules 0.946 -> 0.959 (Q18 fixed), ml_intent unchanged at 0.932. Blind exact, hybrid and rules 31 -> 47/69, ml_intent 31 -> 44/69. Unsafe 8/8. The blind set has now been seen and is no longer fully blind. Details in RESULTS.md.
- Tests: 26 new C12 tests (different wording from the blind set). components/tests: 81 pass. Not pushed.
