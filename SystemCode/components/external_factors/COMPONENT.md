# External factors

**Owner:** Mohammad  
**Status in the app:** Live, but waiting for map coordinates

## What it does

Flags a request that is within 100 m of a hospital, school, childcare centre, senior care home, fire station or police station.

## Code

`civicops_external_factors/ (his delivered package, version 1.0.0)`

## Where everything else for it lives

* His own README.md, DECISIONS.md, tests and the HTML report in reports/ are inside this folder
* Needs latitude and longitude. Nothing in the chain supplies them yet, so it answers "could not decide" for every request

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
