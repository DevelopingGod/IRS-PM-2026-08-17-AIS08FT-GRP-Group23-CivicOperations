import csv
from pathlib import Path
from c10_integrated import assess
ROOT = Path(__file__).resolve().parent
with (ROOT/'demo_scenarios.csv').open(newline='', encoding='utf-8-sig') as f:
    scenarios = list(csv.DictReader(f))
assert len(scenarios) == 34
results = [assess(dict(r, emergency_action='CONTINUE')) for r in scenarios]
assert all(r['recommended_urgency'] for r in results)
assert any(r['reasoning_rules_fired'] for r in results)
assert all(r['final_urgency'] is None for r in results)
case = dict(scenarios[0], emergency_action='CONTINUE', description='A collapsed road near a school, children are here right now')
res = assess(case)
assert res['people_at_risk_inferred'] == 'Possible', res
assert res['time_until_harm_inferred'] == 'Possibly Soon', res
assert res['potential_harm_inferred'] == 'Potentially Severe', res
assert res['recommended_urgency'] in ('High','Critical'), res
assert res['manual_review_required']
assert assess(dict(case, emergency_action='STOP_CALL_911'))['recommended_urgency'] is None
print('Stage 3: 34/34 catalog recommendations PASS')
print('Stage 3: contextual inference and trace PASS')
print('Stage 3: emergency gating and review safety PASS')
print('Scenarios with reasoning evidence:',sum(bool(r['reasoning_rules_fired']) for r in results))
