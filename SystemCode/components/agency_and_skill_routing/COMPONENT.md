# Agency and skill routing

**Owner:** Mohammad  
**Status in the app:** Live

## What it does

Decides which city agency handles a request and which crew skill it needs.

## Code

`civicops_agency/ (his delivered runtime, version 1.1.0)`

## Where everything else for it lives

* His own README.md, tests and evidence are inside this folder

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
