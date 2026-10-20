# C12 Admin Q&A - data register

## 2026-10-15 - Frozen admin snapshot (data/sample/c12_gold/snapshot.csv, 2,159 rows, 361 KB)
- Origin: data/processed/requests_real.parquet (real NYC 311, 49,636 rows, 2025-10-19 to 2026-10-11; git-ignored). Built by `tools/c12_admin_qa_eval.py snapshot`, seed 2026.
- Rows: all 1,759 not-closed rows plus 400 closed rows sampled at random.
- Real columns: request_id, label (request_type_label), request_type (code), agency, borough, created_at, closed_at, complaint_family.
- Mapped: status (Closed -> RESOLVED, Open -> SUBMITTED, Pending/Unspecified -> PROCESSED, Assigned -> DISPATCHED, In Progress/Started -> ON_SITE); resolution_status = outcome_class (crew-note proxy).
- Derived: urgency_level = catalog default_urgency (not live C10); eta_low_hours / eta_high_hours = per-type p25 / p75 of real resolution hours (open rows only).
- SYNTHETIC: queue_position (urgency, then age), priority_band (position share 10/35/70%), priority_reasons (text), crew (skill + random 01-03, dispatched/on-site rows), credibility_score (Beta(5,5) x 100, random, no citizen link).
- No personal data: no address, coordinates, BBL, citizen id, or free text.

## 2026-10-15 - History sample (data/sample/c12_gold/history_sample.csv, 1,200 rows, 101 KB)
- Origin: closed rows of the same parquet, random sample, seed 2027. Columns: request_id, request_type, complaint_family, borough, agency, created_at, closed_at. All real.

## 2026-10-15 - Gold questions (data/sample/c12_gold/gold_questions.csv, 74 rows)
- Hand-written by Nabeel (with AI help) before any strategy ran. Each row has a category, the gold intent, and a gold plan (filters, group_by, n, metric, period, request_id).
- Gold answers: data/sample/c12_gold/gold_answers.json, rebuilt by `tools/c12_admin_qa_eval.py gold` (git-ignored, 367 KB).

## Outputs
- research/experiments/c12_benchmark.csv (per strategy, with CIs) and research/experiments/c12_per_question.csv. Rerun on 2026-10-15 after guarded_sql was removed (3 strategies).
