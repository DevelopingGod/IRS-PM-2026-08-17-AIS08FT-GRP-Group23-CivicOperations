"""C11 explainable, deterministic agency and municipality queues (research prototype).
Weights are provisional design assumptions, not NYC operational policy.
"""
import csv
from datetime import datetime, timezone
from pathlib import Path

LEVELS = ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')
PREFIX = {'CRITICAL':'C','HIGH':'H','MEDIUM':'M','LOW':'L'}
WEIGHTS = {'waiting':25, 'sla':35, 'escalation':25, 'sensitive':15}
# Provisional C05 corroboration bonus; Slide 7 weights are addressed in Comment 5.
CORROBORATION_MAX = 10.0
ESCALATION_LEVELS = {'NONE': 0.0, 'SUPERVISOR': 0.5, 'CENTRAL_AUTHORITY': 1.0}

def parse_time(value):
    if isinstance(value, datetime): dt=value
    else: dt=datetime.fromisoformat(str(value).replace('Z','+00:00'))
    if dt.tzinfo is None: raise ValueError('created_date/now must have timezone')
    return dt.astimezone(timezone.utc)

def truth(value):
    """Validate optional evidence; absent is not verified, malformed is an error."""
    if value is None or value == '':
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in ('true', 'yes', '1'):
            return True
        if normalized in ('false', 'no', '0'):
            return False
    raise ValueError(f'Invalid evidence flag: {value!r}')

def corroboration_points(count):
    """Count distinct reporters, not submissions. Missing evidence earns no bonus."""
    if count in (None, ''):
        return 0.0
    if isinstance(count, bool):
        raise ValueError('Invalid corroboration_count')
    try:
        number = int(count)
    except (TypeError, ValueError):
        raise ValueError('Invalid corroboration_count') from None
    if number < 1 or str(count).strip() not in (str(number),):
        raise ValueError('Invalid corroboration_count')
    return (0 if number == 1 else 2.5 if number == 2 else
            5.0 if number <= 4 else 7.5 if number <= 9 else CORROBORATION_MAX)


def escalation_points(row):
    """Do not treat a C07 escalation recommendation as authorization."""
    level = str(row.get('escalation_level') or 'NONE').strip().upper()
    if level not in ESCALATION_LEVELS:
        raise ValueError(f'Invalid escalation_level: {level}')
    approved = truth(row.get('escalation_approved'))
    legacy = truth(row.get('approved_escalation'))
    if approved and level != 'NONE':
        return WEIGHTS['escalation'] * ESCALATION_LEVELS[level], level
    if legacy and row.get('escalation_level') in (None, ''):
        return WEIGHTS['escalation'], 'LEGACY_APPROVED'
    return 0.0, level


def score_request(row, now):
    rid=str(row.get('request_id') or row.get('scenario_id') or '').strip()
    if not rid: raise ValueError('request_id is required')
    agency=str(row.get('agency') or row.get('expected_agency') or '').strip()
    if not agency: raise ValueError(f'{rid}: agency missing')
    status=str(row.get('c11_queue_status') or 'provisional').strip()
    if status not in ('provisional','provisional_review','hold'): raise ValueError(f'{rid}: invalid queue status')
    level=str(row.get('c11_urgency') or row.get('recommended_urgency') or '').strip().upper()
    if status != 'hold' and level not in LEVELS: raise ValueError(f'{rid}: urgency missing/invalid')
    out=dict(row,request_id=rid,agency=agency,urgency_level=level if level in LEVELS else None,c11_urgency=level if level in LEVELS else None,c11_queue_status=status,dispatch_approved=False,dispatch_authorization='not_authorized_by_C11')
    if status=='hold':
        out.update(priority_score=None,priority_reasons=['Held by upstream safety/review gate'],queue_position=None,agency_position=None,priority_band='HOLD')
        return out
    created=row.get('created_date')
    waiting_hours=None
    if created:
        waiting_hours=max(0,(now-parse_time(created)).total_seconds()/3600)
    wait_points=min(WEIGHTS['waiting'],waiting_hours/48*WEIGHTS['waiting']) if waiting_hours is not None else 0
    target=row.get('sla_target_hours')
    sla_points=0
    if target not in (None,''):
        try:
            target=float(target)
        except (TypeError, ValueError):
            raise ValueError(f'{rid}: invalid SLA target') from None
        import math
        if not math.isfinite(target) or target<=0: raise ValueError(f'{rid}: invalid SLA target')
        if waiting_hours is not None:
            sla_points=min(WEIGHTS['sla'],waiting_hours/target*WEIGHTS['sla'])
    escalation, escalation_label = escalation_points(row)
    corroboration = corroboration_points(row.get('corroboration_count'))
    sensitive=WEIGHTS['sensitive'] if truth(row.get('verified_sensitive_site_impact')) else 0
    reasons=[f'Catalog/C10 urgency: {level}']
    if waiting_hours is None: reasons.append('Waiting time unavailable; no waiting points')
    else: reasons.append(f'Waiting: {waiting_hours:.1f}h')
    if target in (None,''): reasons.append('SLA target unavailable; no SLA points')
    else: reasons.append(f'SLA target: {target}h')
    if escalation: reasons.append(f'Approved escalation: {escalation_label}')
    elif escalation_label not in ('NONE',): reasons.append(f'Escalation {escalation_label} unapproved: no escalation points')
    if corroboration: reasons.append(f"C05 independent reporters: {row.get('corroboration_count')} (+{corroboration:g} provisional points)")
    if sensitive: reasons.append('Verified sensitive-site impact')
    if status=='provisional_review': reasons.append('Provisional recommendation requires human review before dispatch')
    reasons.append('Queue position is advisory and does not authorize dispatch')
    out.update(priority_score=round(wait_points+sla_points+escalation+sensitive+corroboration,3),priority_reasons=reasons,waiting_hours=waiting_hours,priority_band='PROVISIONAL_REVIEW' if status=='provisional_review' else 'PROVISIONAL')
    return out

def prioritize(rows, now):
    now=parse_time(now)
    scored=[score_request(r,now) for r in rows]
    ids=[r['request_id'] for r in scored]
    if len(ids)!=len(set(ids)): raise ValueError('Duplicate request IDs')
    active=[r for r in scored if r['c11_queue_status']!='hold']
    active.sort(key=lambda r:(LEVELS.index(r['urgency_level']),-r['priority_score'],parse_time(r['created_date']) if r.get('created_date') else now,r['request_id']))
    global_counts={level:0 for level in LEVELS}
    agency_counts={}
    for r in active:
        level=r['urgency_level'];global_counts[level]+=1
        r['queue_position']=f"{PREFIX[level]}{global_counts[level]}"
        key=(r['agency'],level);agency_counts[key]=agency_counts.get(key,0)+1
        r['agency_position']=f"{PREFIX[level]}{agency_counts[key]}"
    return active+[r for r in scored if r['c11_queue_status']=='hold']

def demo_rows(folder, now):
    with (Path(folder)/'demo_scenarios.csv').open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
    # Synthetic timestamps and SLA targets are for algorithm testing only.
    for i,r in enumerate(rows):
        r.update(request_id=r['scenario_id'],agency=r['expected_agency'],c11_urgency=r['expected_urgency'].upper(),c11_queue_status='provisional',created_date=f'2026-10-{10+i%3:02d}T09:00:00+00:00',sla_target_hours='72')
    return rows

if __name__=='__main__':
    import json
    folder=Path(__file__).resolve().parent
    results=prioritize(demo_rows(folder,None),'2026-10-13T12:00:00+00:00')
    print(json.dumps([{k:r[k] for k in ('request_id','agency','urgency_level','priority_score','queue_position','agency_position')} for r in results],indent=2))
