# Fake request detection

**Owner:** Nabeel  
**Status in the app:** Live

## What it does

Estimates how likely a report is unfounded. It can lower a report or ask a person to check it. It never rejects one.

## Code

`c06_fake.py`

## Where everything else for it lives

* Notebook: research/notebooks/C06_fake.ipynb
* Docs and tests: in components/duplicate_detection/ (one shared results file)
* Results: research/experiments/c06_benchmark.csv

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
