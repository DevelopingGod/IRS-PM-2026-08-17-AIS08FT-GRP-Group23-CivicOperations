"""Stage 3: transparent, provisional inference from explicitly supplied context.
Not official NYC 311 policy; inferred exposure is not verified exposure.
"""
import re

SEVERITY = {'Minor': 0, 'Moderate': 1, 'Serious': 2}
SENSITIVE = re.compile(r'\b(school|hospital|daycare|nursing home|playground)\b', re.I)
EXPOSURE = re.compile(r'\b(children|pedestrians|people|crowd|patients|students|workers)\b', re.I)
SOON = re.compile(r'\b(now|right now|immediately|about to|currently|actively|urgent|within (?:minutes|hours))\b', re.I)
DELAYED = re.compile(r'\b(next week|in a few days|later this week|not urgent)\b', re.I)
HAZARDS = [
    ('HAZ_CAVEIN', re.compile(r'\b(cave.in|sinkhole|collapsed road|road collapse)\b', re.I), 'Serious'),
    ('HAZ_SEWER', re.compile(r'\b(sewage|sewer backup|raw sewer|wastewater)\b', re.I), 'Serious'),
    ('HAZ_FLOOD', re.compile(r'\b(flooding|flooded|water main break|burst water main)\b', re.I), 'Moderate'),
    ('HAZ_TREE', re.compile(r'\b(fallen tree|tree fell|cracked branch|falling branch)\b', re.I), 'Serious'),
    ('HAZ_HYDRANT', re.compile(r'\b(blocked fire hydrant|fire hydrant blocked)\b', re.I), 'Serious'),
    ('HAZ_POTHOLE', re.compile(r'\b(pothole|broken pavement)\b', re.I), 'Moderate'),
]

def infer(request):
    description = str(request.get('description') or request.get('scenario_text') or request.get('citizen_description') or '')
    text = ' '.join([description, str(request.get('complaint_type') or ''), str(request.get('descriptor') or '')])
    evidence = []
    severity = 'Unknown'
    for rid, pattern, level in HAZARDS:
        if pattern.search(text) and (severity == 'Unknown' or SEVERITY[level] > SEVERITY[severity]):
            severity = level
            evidence.append({'rule': rid, 'source': 'request_text', 'verified': False})
    site = str(request.get('sensitive_location') or request.get('sensitive_site_type') or request.get('location_type') or '')
    site_signal = bool(SENSITIVE.search(site) or SENSITIVE.search(description))
    exposure_signal = bool(EXPOSURE.search(description))
    if request.get('people_at_risk_verified') is True:
        people = 'Yes'; evidence.append({'rule':'PEOPLE_VERIFIED', 'source':'people_at_risk_verified', 'verified':True})
    elif request.get('people_at_risk_verified') is False:
        people = 'No'; evidence.append({'rule':'PEOPLE_VERIFIED', 'source':'people_at_risk_verified', 'verified':True})
    elif site_signal or exposure_signal:
        people = 'Possible'; evidence.append({'rule':'PEOPLE_INFERRED', 'source':'sensitive_site_or_description', 'verified':False})
    else:
        people = 'Unknown'
    if request.get('time_until_harm_verified') in ('Soon','Delayed'):
        timing = request['time_until_harm_verified']; evidence.append({'rule':'TIME_VERIFIED','source':'time_until_harm_verified','verified':True})
    elif SOON.search(description):
        timing = 'Possibly Soon'; evidence.append({'rule':'TIME_INFERRED_SOON','source':'request_text','verified':False})
    elif DELAYED.search(description):
        timing = 'Possibly Delayed'; evidence.append({'rule':'TIME_INFERRED_DELAYED','source':'request_text','verified':False})
    else:
        timing = 'Unknown'
    # Infer potential harm conservatively: plausible harm is not a verified fact.
    if severity == 'Serious' and people in ('Yes','Possible') and timing in ('Soon','Possibly Soon'):
        harm = 'Potentially Severe'; rule = 'PH_INFERRED_SEVERE'
    elif severity in ('Serious','Moderate') and (people in ('Yes','Possible') or timing in ('Soon','Possibly Soon')):
        harm = 'Potentially Moderate'; rule = 'PH_INFERRED_MODERATE'
    else:
        harm = 'Unknown'; rule = None
    if rule: evidence.append({'rule':rule,'source':'inferred_facts','verified':False})
    return {'hazard_severity_inferred':severity, 'people_at_risk_inferred':people, 'time_until_harm_inferred':timing, 'potential_harm_inferred':harm, 'inference_evidence':evidence, 'reasoning_rules_fired':[e['rule'] for e in evidence]}
