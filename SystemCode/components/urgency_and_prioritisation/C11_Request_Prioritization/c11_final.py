"""C11 consolidated research prototype: C10 -> official SLA -> score -> aging -> queues.

Policy weights and aging thresholds are experimental, not NYC operational policy.
"""
from datetime import timedelta
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
C10_DIR = ROOT.parent / 'C10_Urgency_Assignment'
if not C10_DIR.is_dir():
    raise FileNotFoundError(f'Shared C10 component folder not found: {C10_DIR}')
sys.path.insert(0, str(C10_DIR))
from c10_urgency_v11_stage6_1 import assess as assess_c10
from c11_prioritization import score_request, parse_time, LEVELS, PREFIX
from c11_sla_lookup import load_official, load_review, lookup
from c11_category_enrichment import enrich

AGING_RULES = ((96.0, 20.0, 'AG02'), (48.0, 10.0, 'AG01'))
BANDS = ('ACT NOW', 'NEXT', 'PLANNED', 'MONITOR')
# Research-only assumption: after 7 days without a matched SLA, minimum NEXT.
NO_SLA_STARVATION_HOURS = 168.0
DEFAULT_BANDS = {'CRITICAL': 'ACT NOW', 'HIGH': 'NEXT', 'MEDIUM': 'PLANNED', 'LOW': 'MONITOR'}


def scheduling_band(item):
    """Separate urgency from dispatch scheduling; never authorize dispatch."""
    if item['c11_queue_status'] == 'hold':
        return 'HOLD', False, None, 'Held by C10; not eligible for queue.'
    urgency = item['urgency_level']
    band = DEFAULT_BANDS[urgency]
    waiting = item.get('waiting_hours')
    overdue = item.get('sla_overdue') is True
    no_sla_aged = (item.get('sla_status') == 'SLA_UNKNOWN' and
                    waiting is not None and waiting >= NO_SLA_STARVATION_HOURS)
    if overdue or no_sla_aged:
        rule = 'STARVE_SLA_OVERDUE' if overdue else 'STARVE_NO_SLA_168H'
        reason = ('Matched official SLA overdue' if overdue else
                  'No matched SLA; waiting at least 168 hours (experimental)')
        if BANDS.index(band) > BANDS.index('NEXT'):
            return 'NEXT', True, rule, reason + ': scheduling floor NEXT.'
        return band, False, rule, reason + ': existing band already NEXT or higher.'
    return band, False, None, 'Default scheduling band from C10 urgency.'

C10_FIELDS = ('recommended_urgency', 'c11_urgency', 'c11_queue_status',
              'final_urgency', 'final_status', 'manual_review_required',
              'review_flags', 'matched_rules', 'reasoning_status')


def prioritize_from_c10(requests, now, root=ROOT):
    """Return global and agency-specific urgency-banded ranked queue.

    Never trusts caller-supplied urgency or SLA hours. Held requests remain unranked.
    Provisional-review requests are ranked but clearly marked as unapproved.
    """
    now = parse_time(now)
    root = Path(root)
    official = load_official(root / 'nyc311_official_sla.csv')
    review = load_review(root / 'c11_water_sewer_SLA_detail_review.csv')
    scored = []
    # Group counts may arrive on any report. Propagate the largest trusted C05
    # count to its primary in this batch. For cross-batch updates, the caller/store
    # must supply the latest incident-level count on the primary record.
    group_counts = {}
    for req in requests:
        group = str(req.get('duplicate_group_id') or '').strip()
        if group and req.get('corroboration_count') not in (None, ''):
            from c11_prioritization import corroboration_points
            corroboration_points(req['corroboration_count'])
            group_counts[group] = max(group_counts.get(group, 1), int(req['corroboration_count']))
    for request in requests:
        row = dict(request)
        for field in C10_FIELDS:
            row.pop(field, None)
        row.update(assess_c10(request, root=C10_DIR))
        relation = str(request.get('relation') or 'NEW').strip().upper()
        if relation not in ('NEW', 'DUPLICATE', 'RECURRENCE'):
            raise ValueError(f'Invalid C05 relation: {relation}')
        primary = request.get('is_primary')
        if primary is not None and not isinstance(primary, bool):
            raise ValueError('is_primary must be a boolean')
        if relation == 'DUPLICATE' or primary is False:
            if not request.get('duplicate_of'):
                raise ValueError('Non-primary duplicate requires duplicate_of')
            row['c11_queue_status'] = 'hold'
            row['duplicate_queue_reason'] = 'Linked to primary incident; no separate dispatch queue item'
        elif relation == 'RECURRENCE':
            row['duplicate_queue_reason'] = 'Recurrence is a new incident; C07 may consider escalation'
        group = str(request.get('duplicate_group_id') or '').strip()
        if group and primary is not False:
            row['corroboration_count'] = group_counts.get(group, request.get('corroboration_count', 1))
        # C10 retains its internal title-case labels; C11 publishes uppercase.
        for field in ('recommended_urgency', 'c11_urgency'):
            if isinstance(row.get(field), str):
                row[field] = row[field].upper()
        row['request_id'] = str(request.get('request_id') or request.get('scenario_id') or '').strip()
        row['agency'] = str(request.get('agency') or request.get('expected_agency') or '').strip()
        if not row['request_id'] or not row['agency']:
            raise ValueError('request ID and agency are required')
        row.pop('sla_target_hours', None)
        row, category_status, category_reason = enrich(row)
        sla = lookup(row, official, review)
        row['sla_category_status'] = category_status
        row['sla_category_reason'] = category_reason
        row.update({k: v for k, v in sla.items() if k != 'request_type_code'})
        row.update(sla_deadline=None, sla_utilization_pct=None,
                   sla_hours_remaining=None, sla_overdue=None)
        target = row['sla_target_hours']
        if target is not None and row.get('created_date'):
            created = parse_time(row['created_date'])
            deadline = created + timedelta(hours=target)
            elapsed = max(0.0, (now - created).total_seconds() / 3600)
            row.update(sla_deadline=deadline.isoformat(),
                       sla_utilization_pct=round(100 * elapsed / target, 2),
                       sla_hours_remaining=round((deadline - now).total_seconds() / 3600, 2),
                       sla_overdue=now > deadline)
        item = score_request(row, now)
        if row.get('duplicate_queue_reason'):
            item['priority_reasons'].append(row['duplicate_queue_reason'])
        item['base_priority_score'] = item['priority_score']
        item['aging_bonus'] = 0.0
        item['aging_rule'] = None
        if item['c11_queue_status'] != 'hold' and item.get('waiting_hours') is not None:
            for threshold, bonus, rule in AGING_RULES:
                if item['waiting_hours'] >= threshold:
                    item['aging_bonus'] = bonus
                    item['aging_rule'] = rule
                    item['priority_score'] = round(item['priority_score'] + bonus, 3)
                    item['priority_reasons'].append(
                        f'{rule}: waiting >= {threshold:g}h; experimental aging +{bonus:g} points')
                    break
        if item['sla_status'] == 'SLA_UNKNOWN':
            item['priority_reasons'].append('Official SLA unknown: no SLA-risk points assigned; ' + item['sla_reason'])
        elif item['sla_utilization_pct'] is not None:
            label = 'Team SLA target' if item['sla_status'] == 'TEAM_TARGET' else 'Official NYC SLA'
            item['priority_reasons'].append(
                f"{label}: {item['sla_original']}; utilization {item['sla_utilization_pct']:.1f}%")
            if item['sla_overdue']:
                item['priority_reasons'].append('Official SLA deadline overdue')
        band, applied, rule, reason = scheduling_band(item)
        item['priority_band'] = band
        item['starvation_protection_applied'] = applied
        item['starvation_rule'] = rule
        item['starvation_reason'] = reason
        item['priority_reasons'].append(reason)
        scored.append(item)
    ids = [r['request_id'] for r in scored]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate request IDs')
    active = [r for r in scored if r['c11_queue_status'] != 'hold']
    active.sort(key=lambda r: (BANDS.index(r['priority_band']), LEVELS.index(r['urgency_level']), -r['priority_score'],
                               parse_time(r['created_date']) if r.get('created_date') else now,
                               r['request_id']))
    global_counts = {level: 0 for level in LEVELS}
    agency_counts = {}
    for overall_index, item in enumerate(active, start=1):
        item['overall_queue_position'] = overall_index
        level = item['urgency_level']
        global_counts[level] += 1
        item['queue_position'] = f'{PREFIX[level]}{global_counts[level]}'
        key = (item['agency'], level)
        agency_counts[key] = agency_counts.get(key, 0) + 1
        item['agency_position'] = f'{PREFIX[level]}{agency_counts[key]}'
    for item in scored:
        if item['c11_queue_status'] == 'hold':
            item['overall_queue_position'] = None
    return active + [r for r in scored if r['c11_queue_status'] == 'hold']
