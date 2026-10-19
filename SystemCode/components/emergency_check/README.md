# Emergency check

**Owner:** Sankalp  
**Status in the app:** Live

## What it does

Screens every message before anything is filed. A 911 situation stops the chat and nothing is stored.

## Code

`c02_emergency.py`

## Where everything else for it lives

* Model: models/c02_emergency_tfidf_lr.joblib
* Evidence: 03_Research/evidence/02_emergency/
* Results: 03_Research/experiments/emergency_sweep_recovered.csv

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
