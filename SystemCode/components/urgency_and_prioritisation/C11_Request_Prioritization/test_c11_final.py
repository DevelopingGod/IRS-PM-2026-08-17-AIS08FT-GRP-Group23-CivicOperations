"""Stage 8 consolidation checks: 34 scenarios, safety, SLA, and aging."""
import csv
from pathlib import Path
from c11_final import prioritize_from_c10
from c11_sla_lookup import load_official, load_review, lookup

ROOT = Path(__file__).resolve().parent
NOW = '2026-10-13T12:00:00+00:00'
with (ROOT / 'demo_scenarios.csv').open(encoding='utf-8-sig', newline='') as f:
    cases = list(csv.DictReader(f))
assert len(cases) == 34
rows = [dict(r, request_id=r['scenario_id'], agency=r['expected_agency'],
             emergency_action='CONTINUE', created_date='2026-10-12T12:00:00+00:00') for r in cases]
ranked = prioritize_from_c10(rows, NOW)
assert len(ranked) == 34 and all(r['c11_urgency'] == r['recommended_urgency'] for r in ranked)
assert all(r['final_urgency'] is None and r['sla_status'] == 'SLA_UNKNOWN' for r in ranked)
assert all(r['queue_position'] for r in ranked)
print('Stage 8: 34/34 C10 -> C11 provisional queues PASS')

base = rows[0]
assert prioritize_from_c10([dict(base, c11_urgency='Critical', recommended_urgency='Critical', sla_target_hours=1)], NOW)[0]['sla_target_hours'] is None
blocked = prioritize_from_c10([dict(base, request_id='BLOCK', emergency_action='STOP_CALL_911')], NOW)[0]
assert blocked['c11_queue_status'] == 'hold' and blocked['queue_position'] is None
print('Stage 8: untrusted urgency/SLA rejected and emergency holds PASS')

old = prioritize_from_c10([dict(base, request_id='OLD', created_date='2026-10-08T12:00:00+00:00')], NOW)[0]
assert old['aging_rule'] == 'AG02' and old['aging_bonus'] == 20 and old['priority_score'] > old['base_priority_score']
print('Stage 8: aging rules integrated PASS')

review = load_review(ROOT / 'c11_water_sewer_SLA_detail_review.csv')
official = load_official(ROOT / 'nyc311_official_sla.csv')
matched = next((r for r in review if lookup({
    'request_type_code': r['request_type_code'], 'official_agency': r['official_agency'],
    'official_problem': r['official_problem'], 'official_problem_details': r['official_problem_details'],
    'official_additional_details': r['additional_details']}, official, review)['sla_status'] == 'MATCHED_EXACT_DETAIL'), None)
assert matched is not None
case = next(r for r in rows if r['request_type_code'] == matched['request_type_code'])
exact = dict(case, request_id='EXACT', agency=matched['official_agency'],
             official_agency=matched['official_agency'], official_problem=matched['official_problem'],
             official_problem_details=matched['official_problem_details'],
             official_additional_details=matched['additional_details'])
result = prioritize_from_c10([exact], NOW)[0]
assert result['sla_status'] == 'MATCHED_EXACT_DETAIL' and result['sla_target_hours'] > 0
assert result['sla_utilization_pct'] is not None
print('Stage 8: official exact-detail SLA integration PASS')
