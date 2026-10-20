# Admin questions

**Owner:** Nabeel  
**Status in the app:** Live

## What it does

Answers staff questions about the queue in plain English, from a read only copy. It never writes and never shows a single reporter's data.

## Code

`c12_admin_qa.py`

## Where everything else for it lives

* Docs: docs/ (results, change log, data register)
* Tests: tests/test_c12_admin_qa.py
* Gold questions: data/sample/c12_gold/
* Results: research/experiments/c12_benchmark.csv, research/experiments/c12_per_question.csv
* Tools: tools/c12_admin_qa_eval.py

## How the app calls it

The app reaches every component through one adapter in `app/backend/plugins/registry.py`. The adapter only renames shared fields and calls the owner's code. If the component fails, the app uses the safe default written in the component specification.
