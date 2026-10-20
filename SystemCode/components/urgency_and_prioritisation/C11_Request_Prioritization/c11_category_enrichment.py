"""Verified official-category enrichment for C11 (research prototype).

Never derive an official category from an RT code alone. A trusted upstream
mapper/reviewer must supply verified_official_category with evidence source.
"""

REQUIRED = ('official_agency', 'official_problem', 'official_problem_details')
OPTIONAL = 'official_additional_details'


def enrich(request):
    row = dict(request)
    category = request.get('verified_official_category')
    if category is None:
        return row, 'NOT_PROVIDED', 'No verified official category supplied; no RT-code guessing.'
    if not isinstance(category, dict):
        return row, 'INVALID', 'Verified official category must be a dictionary.'
    if category.get('verified') is not True or not str(category.get('source') or '').strip():
        return row, 'UNVERIFIED', 'Category must have verified=true and a nonempty evidence source.'
    if not all(isinstance(category.get(k), str) and category[k].strip() for k in REQUIRED):
        return row, 'INCOMPLETE', 'Verified category missing official agency, problem or details.'
    for key in REQUIRED + (OPTIONAL,):
        if key in category:
            row[key] = category[key].strip() if isinstance(category[key], str) else category[key]
    return row, 'VERIFIED', f"Verified official category from {category['source']}."
