# Credibility and escalation

**Owner:** Sankalp  
**Status in the app:** Live

## What it does

Learns how often each reporter has been right before, compared with what is normal for the kind of problem they report, and escalates a problem that keeps coming back.

## Code

`c06_credibility.py`

## Where everything else for it lives

* Evidence: 03_Research/evidence/03_credibility/
* Results: 03_Research/experiments/c06_parameter_sweep.csv
* Tools: 06_Workspace_Tools/sweep_c06_credibility.py

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
