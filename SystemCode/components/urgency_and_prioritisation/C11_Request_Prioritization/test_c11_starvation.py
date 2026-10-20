"""Sankalp Comment 2 regression checks; run from C11 folder with sibling C10."""
from pathlib import Path
from c11_final import prioritize_from_c10
from c11_prioritization import parse_time

ROOT = Path(__file__).resolve().parent
NOW = '2026-10-15T12:00:00+00:00'

# Use catalog-defined RT codes and force a controlled test of the scheduling policy.
from c10_catalog_mapping import load_catalog
catalog = load_catalog(ROOT.parent / 'C10_Urgency_Assignment' / 'request_catalog.csv')
by_level = {}
for code, data in catalog.items():
    by_level.setdefault(data['baseline_urgency'].upper(), code)
assert 'LOW' in by_level and 'MEDIUM' in by_level, by_level

base = {'agency': 'TEST', 'emergency_action': 'CONTINUE',
        'credibility_score': 80, 'created_date': '2026-10-15T11:55:00+00:00'}
old = dict(base, request_id='OLD_LOW', request_type_code=by_level['LOW'],
           created_date='2026-09-15T12:00:00+00:00')
new = dict(base, request_id='NEW_MEDIUM', request_type_code=by_level['MEDIUM'])
ranked = prioritize_from_c10([new, old], NOW)
assert ranked[0]['request_id'] == 'OLD_LOW', ranked
assert ranked[0]['urgency_level'] == 'LOW'
assert ranked[0]['priority_band'] == 'NEXT'
assert ranked[0]['starvation_protection_applied'] is True
assert ranked[0]['overall_queue_position'] == 1
assert ranked[1]['urgency_level'] == 'MEDIUM'
assert ranked[1]['priority_band'] == 'PLANNED'
assert ranked[0]['dispatch_approved'] is False
print('PASS: old LOW outranks fresh MEDIUM by NEXT floor without changing urgency')

held = dict(old, request_id='HELD', emergency_action='STOP_CALL_911')
ranked = prioritize_from_c10([new, held], NOW)
assert ranked[-1]['priority_band'] == 'HOLD'
assert ranked[-1]['overall_queue_position'] is None
assert ranked[-1]['queue_position'] is None
print('PASS: emergency holds remain unranked')

# Isolated policy test: verified overdue SLA has NEXT floor even for LOW urgency.
from c11_final import scheduling_band
example = {'c11_queue_status': 'provisional', 'urgency_level': 'LOW',
           'sla_status': 'MATCHED_EXACT_DETAIL', 'sla_overdue': True,
           'waiting_hours': 30}
band, applied, rule, _ = scheduling_band(example)
assert (band, applied, rule) == ('NEXT', True, 'STARVE_SLA_OVERDUE')
print('PASS: overdue official SLA raises scheduling band to NEXT')
