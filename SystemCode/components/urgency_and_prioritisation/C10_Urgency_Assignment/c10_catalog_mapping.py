"""Stage 1: catalog-backed baseline lookup for C10 (research prototype)."""
import csv
from pathlib import Path

LEVELS = {'LOW': 'Low', 'MEDIUM': 'Medium', 'HIGH': 'High', 'CRITICAL': 'Critical'}


def load_catalog(path):
    """Load standardized codes; fail on missing/duplicate codes or invalid levels."""
    catalog = {}
    with Path(path).open(newline='', encoding='utf-8-sig') as stream:
        for row in csv.DictReader(stream):
            code = (row.get('request_type_code') or '').strip().upper()
            level = (row.get('default_urgency') or '').strip().upper()
            if not code or code in catalog or level not in LEVELS:
                raise ValueError(f'Invalid/duplicate catalog row: {code!r} {level!r}')
            catalog[code] = {'baseline_urgency': LEVELS[level], 'citizen_label': row.get('citizen_label', ''), 'agency': row.get('agency', '')}
    return catalog


def assign_baseline(request, catalog):
    """Return a *baseline*, not a validated final urgency."""
    code = str(request.get('request_type_code') or '').strip().upper()
    match = catalog.get(code)
    if not match:
        return {'request_type_code': code or None, 'baseline_urgency': None, 'catalog_match': False,
                'manual_review_required': True, 'reason': 'Unknown or missing request type code'}
    return {'request_type_code': code, **match, 'catalog_match': True,
            'manual_review_required': False, 'reason': 'Baseline from team request catalog; contextual assessment pending'}
