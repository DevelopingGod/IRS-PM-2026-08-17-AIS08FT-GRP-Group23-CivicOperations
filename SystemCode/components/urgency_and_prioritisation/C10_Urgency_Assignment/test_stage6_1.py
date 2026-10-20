"""Stage 6.1 evidence-based Critical tests; run from the C10 folder."""
import csv
from pathlib import Path
from c10_urgency_v11_stage6_1 import assess
ROOT=Path(__file__).resolve().parent
cases=list(csv.DictReader((ROOT/'demo_scenarios.csv').open(encoding='utf-8-sig',newline='')))
assert len(cases)==34
for row in cases:
    r=assess({**row,'emergency_action':'CONTINUE'})
    assert r['recommended_urgency'] in ('LOW','MEDIUM','HIGH','CRITICAL')
    assert r['final_urgency'] is None and r['c11_urgency']==r['recommended_urgency']
print('Stage 6.1: 34/34 demo contracts PASS')
for code,desc in [('RT002','Street cave-in'),('RT060','Branch Cracked and Will Fall')]:
    base={'request_type_code':code,'description':desc,'emergency_action':'CONTINUE'}
    unverified=assess(base)
    assert unverified['recommended_urgency']!='CRITICAL',unverified
    verified=assess({**base,'verified_significant_exposure':True,'verified_near_term_harm':True})
    assert verified['recommended_urgency']=='CRITICAL',verified
    assert 'CR06_EVIDENCE_BASED' in verified['matched_rules']
    assert verified['manual_review_required'] and verified['final_urgency'] is None
    assert verified['c11_queue_status']=='provisional_review'
    blocked=assess({**base,'verified_significant_exposure':True,'verified_near_term_harm':True,'emergency_action':'STOP_CALL_911'})
    assert blocked['c11_queue_status']=='hold' and blocked['recommended_urgency'] is None
    for flags in ({'credibility_score':0},{'suspected_fake_request':True}):
        assert assess({**base,'verified_significant_exposure':True,'verified_near_term_harm':True,**flags})['recommended_urgency']=='CRITICAL'
print('Stage 6.1: evidence-gated Critical and emergency safety PASS')
print('Stage 6.1: credibility/fake non-downgrade PASS')
