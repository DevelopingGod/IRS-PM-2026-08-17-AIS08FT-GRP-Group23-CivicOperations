# Duplicate detection

**Owner:** Nabeel  
**Status in the app:** Live

## What it does

Links a new report to an open report of the same problem, so only one crew goes, and marks a problem that returns after it was closed.

## Code

`c05_duplicate.py`

## Where everything else for it lives

* Notebook: research/notebooks/C05_duplicate.ipynb
* Docs: docs/ (results, change log, data register, labelling rules. Shared with Fake request detection)
* Tests: tests/test_c05_c06.py (covers Fake request detection too)
* Results: research/experiments/c05_benchmark.csv, research/experiments/c05_c06_benchmark_meta.json
* Labelling kit: data/sample/c05_label_kit/
* Tools: tools/c05_c06_eval.py, tools/c05_labelling.py

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
