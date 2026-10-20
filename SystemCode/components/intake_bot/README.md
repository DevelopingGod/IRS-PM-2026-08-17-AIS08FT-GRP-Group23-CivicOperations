# Intake bot

**Owner:** Sankalp  
**Status in the app:** Live

## What it does

Turns a citizen's message into a structured request: the type of problem, the address, borough and time, and what is still missing.

## Code

`c01_nlp_bot.py`

## Where everything else for it lives

* Notebooks: 03_Research/notebooks/02_nlp_model_evaluation.ipynb, 03_Research/notebooks/03_nlp_experiment_v2.ipynb
* Models: models/c01_intent_minilm_logreg.joblib, models/c01_intent_tfidf_svm.joblib, models/c01_slot_tagger/
* Evidence: 03_Research/evidence/01_intent/
* Results: 03_Research/experiments/c01_slot_tagger_errors.csv, 03_Research/experiments/slot_label_repair.csv
* Tools: 06_Workspace_Tools/train_c01_slot_tagger.py, 06_Workspace_Tools/label_gold_spans.py, 06_Workspace_Tools/repair_slot_labels.py

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
## Address reading repair, 18 Oct 2026

Two reports of the same pothole were not linked by Duplicate detection, because this component read
"450 Atlantic Avenue," as "450 Atlantic". The word tagger was given words with punctuation still
attached, and it dropped them. The fix: punctuation is split off before tagging, an address that
stops one word early is completed with the street type that follows, Queens house numbers like
37-12 are kept whole, more street types are known, a borough word inside a street name (Manhattan Ave
in Brooklyn) is no longer taken as the borough, and odd input never crashes.

| Measured on | Before | After |
|---|---|---|
| Hand labelled messages (308): address read exactly | 28 of 29 | 29 of 29 |
| Hand labelled messages: cross street read exactly | 3 of 3 | 3 of 3 |
| Hand labelled messages: time phrase read exactly | 37 of 45 | 37 of 45 |
| Everyday address phrasings in `tests/test_address_reading.py` | 7 of 15 | 15 of 15 |

The tests in `tests/test_address_reading.py` keep these from coming back.
