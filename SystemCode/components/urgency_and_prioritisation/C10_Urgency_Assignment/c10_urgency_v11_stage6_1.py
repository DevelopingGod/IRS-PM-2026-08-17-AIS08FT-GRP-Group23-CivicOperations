"""C10 consolidated rule-based urgency assessment (research prototype).

One assessment workflow. Catalog urgency is a baseline; contextual evidence may
raise but never lower it. This is NOT an approved NYC 311 policy.
"""
from pathlib import Path
from c10_catalog_mapping import load_catalog, assign_baseline
from c10_reasoning import infer

ROOT = Path(__file__).resolve().parent
ORDER = {'Low': 0, 'Medium': 1, 'High': 2, 'Critical': 3}
CLEAR = {'CONTINUE', 'cleared_non_emergency'}


def _assess_internal(request, root=ROOT):
    """Assess a 311 request and return a traceable, provisional C11 contract."""
    if not isinstance(request, dict):
        raise TypeError('request must be a dictionary')
    catalog = load_catalog(Path(root) / 'request_catalog.csv')
    baseline = assign_baseline(request, catalog)
    result = {
        **baseline,
        'recommended_urgency': None,
        'final_urgency': None,
        'final_status': 'not_approved',
        'c11_urgency': None,
        'c11_queue_status': 'hold',
        'urgency_source': 'none',
        'manual_review_required': True,
        'review_flags': [],
        'reasoning_status': 'not_evaluated',
        'matched_rules': [],
        'model_status': 'unvalidated_rule_based_research_prototype',
    }

    # 1. Emergency clearance must precede ordinary 311 triage.
    action = request.get('emergency_action', request.get('emergency_check'))
    if action not in CLEAR:
        result['review_flags'].append('emergency_clearance_required')
        result['reasoning_status'] = 'blocked_emergency_check'
        return result
    if request.get('verified_imminent_harm') is True:
        result['review_flags'].append('possible_emergency_recheck')
        result['reasoning_status'] = 'blocked_emergency_recheck'
        return result

    # 2. Standardized C01 request type determines the baseline.
    if not baseline['catalog_match']:
        result['review_flags'].append('unknown_request_type_code')
        return result
    result['recommended_urgency'] = baseline['baseline_urgency']
    result['urgency_source'] = 'request_catalog'
    result['matched_rules'].append('CATALOG_BASELINE')
    result['reasoning_status'] = 'baseline_only'

    # 3. Validate non-safety inputs. Credibility/fake flags NEVER lower urgency.
    score = request.get('credibility_score')
    if score is not None:
        try:
            if isinstance(score, bool):
                raise ValueError('boolean is not a score')
            score = float(score)
            if not 0 <= score <= 100:
                raise ValueError('score out of range')
        except (TypeError, ValueError):
            result['review_flags'].append('invalid_credibility_score')
        else:
            if score < 30:
                result['review_flags'].append('low_credibility_review_only')
    if request.get('suspected_fake_request') in (True, 'Yes'):
        result['review_flags'].append('suspected_fake_review_only')

    # 4. Infer contextual evidence; inferred facts are not verified facts.
    inferred = infer(request)
    result.update(inferred)
    result['people_at_risk'] = inferred['people_at_risk_inferred']
    result['time_until_harm'] = inferred['time_until_harm_inferred']
    result['potential_harm'] = inferred['potential_harm_inferred']
    result['hazard_severity'] = inferred['hazard_severity_inferred']
    result['matched_rules'].extend(inferred['reasoning_rules_fired'])

    # 5. Safety: explicit emergency signals require a new C02 check.
    # Text mentioning urgency alone is not proof of an emergency.
    if request.get('emergency_recheck_required') is True:
        result['recommended_urgency'] = None
        result['c11_queue_status'] = 'hold'
        result['reasoning_status'] = 'blocked_emergency_recheck'
        result['review_flags'].append('refer_to_emergency_check')
        return result

    # 6. Provisional IF-THEN escalation; no automatic Critical on inferred text.
    if inferred['potential_harm_inferred'] == 'Potentially Severe':
        if ORDER[result['recommended_urgency']] < ORDER['High']:
            result['recommended_urgency'] = 'High'
            result['urgency_source'] = 'catalog_plus_reasoning'
            result['reasoning_status'] = 'contextual_inference_escalation'
            result['matched_rules'].append('INFERRED_SEVERE_REVIEW')
            result['review_flags'].append('inferred_severe_harm_requires_review')
        else:
            result['reasoning_status'] = 'contextual_evidence_considered_baseline_retained'
    elif inferred['reasoning_rules_fired']:
        result['reasoning_status'] = 'contextual_evidence_considered_baseline_retained'

    # CR06 evidence-based Critical: hazard type alone never establishes Critical.
    # Verified exposure and near-term harm, or verified major sensitive-site
    # disruption, are required. Emergency indications always take precedence.
    code = baseline['request_type_code']
    text = ' '.join(str(request.get(k) or '') for k in
                    ('description', 'scenario_text', 'citizen_description',
                     'complaint_type', 'descriptor')).casefold()
    cavein = code == 'RT002' and any(x in text for x in
                 ('cave-in', 'cave in', 'sinkhole', 'collapsed road', 'road collapse'))
    branch = any(x in text for x in ('branch cracked and will fall',
                 'falling branch', 'branch about to fall'))
    # RT060 is a fallen-tree catalog code; it is not itself proof of a cracked branch.
    hazard = cavein or (code == 'RT060' and branch)
    verified_exposure = (request.get('verified_significant_exposure') is True
                         or request.get('verified_people_exposure') is True
                         or request.get('people_at_risk_verified') is True)
    verified_soon = (request.get('verified_near_term_harm') is True
                     or request.get('time_until_harm_verified') == 'Soon')
    verified_disruption = (request.get('verified_major_sensitive_site_disruption') is True
                           and request.get('sensitive_site_impact_confirmed') is True)
    if hazard and ((verified_exposure and verified_soon) or verified_disruption):
        result['recommended_urgency'] = 'Critical'
        result['urgency_source'] = 'catalog_plus_reasoning'
        result['reasoning_status'] = 'verified_context_critical_escalation'
        result['matched_rules'].append('CR06_EVIDENCE_BASED')
        result['review_flags'].append('critical_requires_human_review')
        result['critical_evidence'] = {
            'hazard_type': 'cave_in' if cavein else 'falling_branch',
            'verified_exposure': verified_exposure,
            'verified_near_term_harm': verified_soon,
            'verified_sensitive_site_disruption': verified_disruption,
        }
    elif hazard:
        result['review_flags'].append('critical_hazard_evidence_incomplete')

    # Critical remains provisional and requires human review.
    if result['recommended_urgency'] == 'Critical':
        result['review_flags'].append('critical_requires_human_review')
    result['review_flags'] = list(dict.fromkeys(result['review_flags']))
    result['manual_review_required'] = bool(result['review_flags'])
    result['c11_urgency'] = result['recommended_urgency']
    result['c11_queue_status'] = ('provisional_review' if result['manual_review_required']
                                  else 'provisional')
    return result


def assess(request, root=ROOT):
    """Public C10/C11 contract: uppercase urgency, unchanged internal reasoning."""
    result = _assess_internal(request, root=root)
    for field in ('baseline_urgency', 'recommended_urgency', 'final_urgency', 'c11_urgency'):
        value = result.get(field)
        if isinstance(value, str) and value.upper() in ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL'):
            result[field] = value.upper()
    return result
