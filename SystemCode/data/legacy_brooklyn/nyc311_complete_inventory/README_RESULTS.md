# NYC311 crawl results

Run date: 2026-09-11

## Coverage

- 1,080 deduplicated pages
- 899 NYC311 knowledge articles
- 165 external form or agency pages
- 82 pages containing extractable form controls
- 1,736 extracted fields
- 281 fields explicitly marked required
- 59 portal articles containing structured “What You Need” prose
- 15 unresolved browser-only or access-controlled destinations

## Files

- `nyc311_pages.csv`: page routes, article IDs, agencies, report links, prose requirements, and errors
- `nyc311_fields.csv`: labels, names, control types, required evidence, options, placeholders, and help text
- `nyc311_inventory.json`: full machine-readable records
- `external_report_links.csv`: source-to-external-destination mapping
- `browser_only_gaps.csv`: the 15 URLs that blocked static retrieval
- `crawl_stats.json`: coverage statistics

## Accuracy note

The NYC311 knowledge portal describes requirements but usually redirects users
to separate agency systems for intake. Portal feedback and lookup controls were
removed from the field dataset. `required=true` is used only where the form's
HTML, ARIA metadata, or visible label explicitly marks the field required.
Conditional fields that appear only after user interaction require browser-based
branch exploration and should not be inferred from the static result.
