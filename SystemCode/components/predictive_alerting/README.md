# Predictive alerting

**Owner:** Sankalp  
**Status in the app:** Live

## What it does

Warns staff that a borough will be busy tomorrow, from the weather forecast, before the calls arrive.

## Code

`c11_alerting.py`

## Where everything else for it lives

* Model: models/c11_demand_model.joblib, models/c11_manifest.json
* Evidence: 03_Research/evidence/04_alerting/
* Results: 03_Research/experiments/c11_demand_arms.csv
* Tools: 06_Workspace_Tools/train_c11_alerting.py

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
