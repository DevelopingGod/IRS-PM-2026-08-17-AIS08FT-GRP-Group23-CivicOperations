"""Consolidated C10 tests; run from the folder containing catalog and scenario CSVs."""
import csv
from pathlib import Path
from c10_urgency_v11 import assess

ROOT = Path(__file__).resolve().parent
with (ROOT / 'demo_scenarios.csv').open(newline='', encoding='utf-8-sig') as f:
    cases = list(csv.DictReader(f))
assert len(cases) == 34, f'Expected 34 cases, got {len(cases)}'
for case in cases:
    out = assess(dict(case, emergency_action='CONTINUE'))
    assert out['catalog_match'] and out['recommended_urgency'] in {'Low', 'Medium', 'High', 'Critical'}, case
    assert out['c11_urgency'] == out['recommended_urgency']
    assert out['c11_queue_status'] in ('provisional', 'provisional_review')
    assert out['final_urgency'] is None and out['final_status'] == 'not_approved'
print('Consolidated: 34/34 provisional catalog/C11 contracts PASS')

base = dict(cases[0], emergency_action='CONTINUE')
assert assess(dict(base, emergency_action='STOP_CALL_911'))['c11_queue_status'] == 'hold'
assert assess(dict(base, emergency_action='CONFIRM_WITH_CITIZEN'))['c11_queue_status'] == 'hold'
assert assess(dict(base, verified_imminent_harm=True))['c11_queue_status'] == 'hold'
assert assess(dict(base, request_type_code='UNKNOWN'))['c11_queue_status'] == 'hold'
print('Consolidated: emergency/unknown code safety PASS')

original = assess(base)['recommended_urgency']
for change in ({'credibility_score': 0}, {'suspected_fake_request': True},
               {'credibility_score': 999}):
    out = assess(dict(base, **change))
    assert out['recommended_urgency'] == original
    assert out['manual_review_required']
print('Consolidated: credibility/fake non-downgrade PASS')

context = dict(base, scenario_text='A sinkhole near a school with children nearby is actively growing.',
               emergency_action='CONTINUE')
out = assess(context)
assert out['reasoning_rules_fired'] and out['inference_evidence']
assert out['recommended_urgency'] in {'High', 'Critical'}
assert out['final_urgency'] is None
print('Consolidated: explainable contextual reasoning PASS')
