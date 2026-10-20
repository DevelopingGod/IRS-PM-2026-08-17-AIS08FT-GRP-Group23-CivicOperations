# Urgency + Prioritisation

**Owner:** Khalid  
**Status in the app:** Live

## What it does

Urgency decides how urgent each request is. Prioritisation ranks every open request against each other into one queue.

## Code

`C10_Urgency_Assignment/ and C11_Request_Prioritization/ (copied from his branch)`

## Where everything else for it lives

* His code lives on GitHub on origin/khalid_code. This folder holds a committed copy at a pinned commit, so main has every component and runs as cloned.
* Refresh it with: py -m app.backend.vendor_export (the pin is in app/backend/vendor_export.py, the versions used are in components/pins.json)

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.

## ml_experiment/

`ml_experiment/c10_urgency.py` is Khalid's earlier machine learning prototype for Urgency (dummy,
logistic regression, naive Bayes, linear SVM and random forest on September NYC 311 data), uploaded
to main on 11 Oct 2026. It is kept here unchanged as experiment evidence. Its results are in
`research/experiments/c10_comparison.csv`, `models/c10_comparison.json` and `research/evidence/06_urgency/`.
The app runs his rule based version from the folders above, not this file.
