"""Exact-detail NYC 311 SLA lookup; never guess ambiguous SLA commitments."""
import csv
import re
from pathlib import Path

BASE = Path(__file__).resolve().parent

def normalize(value):
    return ' '.join(str(value or '').casefold().split())

def hours(value):
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*(hours?|days?)\s*', str(value or ''), re.I)
    if not match:
        return None
    amount = float(match.group(1))
    return amount * (24 if match.group(2).lower().startswith('day') else 1)

def load_official(path=BASE / 'nyc311_official_sla.csv'):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))

def load_review(path=BASE / 'c11_water_sewer_SLA_detail_review.csv'):
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        return list(csv.DictReader(stream))

def lookup(request, official, review):
    """Require code plus exact official agency/problem/detail and optional additional detail.

    A team code by itself cannot select an official SLA. If official fields are
    absent or more than one distinct official record matches, return UNKNOWN.
    """
    code = normalize(request.get('request_type_code')).upper()
    agency = normalize(request.get('official_agency') or request.get('agency'))
    problem = normalize(request.get('official_problem'))
    detail = normalize(request.get('official_problem_details'))
    additional = normalize(request.get('official_additional_details'))
    base = {'request_type_code': code or None, 'sla_status': 'SLA_UNKNOWN',
            'sla_target_hours': None, 'sla_original': None, 'sla_source': 'NYC 311 published SLA reference',
            'sla_match_count': 0}
    # Team extension (19 Oct 2026): NYC publishes no SLA for some request types, such as street
    # potholes and street lights. For those the team sets a target, the median time NYC took to
    # close that type in our 311 sample, and it is labelled as a team target, never as official.
    team = [r for r in review if normalize(r.get('request_type_code')).upper() == code
            and normalize(r.get('mapping_status')).upper() == 'TEAM_TARGET']
    if team and hours(team[0].get('official_sla')):
        return dict(base, sla_status='TEAM_TARGET', sla_target_hours=hours(team[0]['official_sla']),
                    sla_original=team[0]['official_sla'], sla_match_count=0,
                    sla_source='Team target: median close time in the NYC 311 sample',
                    sla_reason='No published NYC SLA for this type, team target used')
    if not all((code, agency, problem, detail)):
        return dict(base, sla_reason='Need code and official agency/problem/problem details')
    candidates = [r for r in review if normalize(r.get('request_type_code')).upper() == code
                  and normalize(r.get('official_agency')) == agency
                  and normalize(r.get('official_problem')) == problem
                  and normalize(r.get('official_problem_details')) == detail
                  and normalize(r.get('additional_details')) == additional]
    if not candidates:
        return dict(base, sla_reason='No reviewed code-to-official-category mapping')
    matching = [r for r in official if normalize(r.get('Agency')) == agency
                and normalize(r.get('Problem')) == problem
                and normalize(r.get('Problem_Details')) == detail
                and normalize(r.get('Additional Details')) in (additional, 'n/a' if not additional else additional)]
    unique = {(normalize(r.get('SLA')), normalize(r.get('Additional Details'))) for r in matching}
    if len(unique) != 1:
        return dict(base, sla_match_count=len(matching), sla_reason='Ambiguous or missing official SLA records')
    value = matching[0].get('SLA')
    duration = hours(value)
    if duration is None or duration <= 0:
        return dict(base, sla_match_count=len(matching), sla_reason='Official SLA not a supported numeric duration')
    return dict(base, sla_status='MATCHED_EXACT_DETAIL', sla_target_hours=duration,
                sla_original=value, sla_match_count=len(matching),
                sla_reason='Exact code-to-agency/problem/detail mapping and unique official SLA')
