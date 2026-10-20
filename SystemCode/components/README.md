# components

One folder per component, named after the component. Twelve components make up the chain. All twelve are delivered and live in the app. Resolution time and the Profanity filter were optional in the proposal and are out of scope.

| Component | Folder | Owner | Status |
|---|---|---|---|
| Intake bot | [`intake_bot/`](intake_bot/) | Sankalp | Live |
| Emergency check | [`emergency_check/`](emergency_check/) | Sankalp | Live |
| Credibility and escalation | [`credibility_and_escalation/`](credibility_and_escalation/) | Sankalp | Live |
| Predictive alerting | [`predictive_alerting/`](predictive_alerting/) | Sankalp | Live |
| Duplicate detection | [`duplicate_detection/`](duplicate_detection/) | Nabeel | Live |
| Fake request detection | [`fake_request_detection/`](fake_request_detection/) | Nabeel | Live |
| Admin questions | [`admin_questions/`](admin_questions/) | Nabeel | Live |
| Agency and skill routing | [`agency_and_skill_routing/`](agency_and_skill_routing/) | Mohammad | Live |
| External factors | [`external_factors/`](external_factors/) | Mohammad | Live |
| Crew deployment and routing | [`crew_deployment_and_routing/`](crew_deployment_and_routing/) | Mohammad | Live, plans the whole queue; city staff approve each route |
| Urgency + Prioritisation | [`urgency_and_prioritisation/`](urgency_and_prioritisation/) | Khalid | Live |

Urgency + Prioritisation is counted as two components.

## Shared files

| File | What it is |
|---|---|
| `__init__.py` | Loads every Python component, and keeps the old flat names working, for example `from components import c01_nlp_bot` |
| `common.py` | The result and reason types every component returns |
| `example.py` | A runnable tour: `py -m components.example` |
| `pins.json` | The exact teammate versions the app runs, written by `py -m app.backend.vendor_export` |

## Run the tests

```
py -m pytest
```

Run it inside `01_Application`. That runs Nabeel's tests, Admin questions' tests and the app's tests. Sankalp's four components are checked against their gates with `py 06_Workspace_Tools/verify_components.py`, from the top folder. Mohammad's and Khalid's folders carry their own tests: run `py -m pytest` inside each of them (for Crew deployment, set `PYTHONPATH=src` first).

## Using the Python components directly

### Install

```
py -m pip install scikit-learn sentence-transformers pandas pyarrow joblib
```

`torch` comes with `sentence-transformers`. A GPU is not needed: the whole intake
component runs in about 60 ms per message on a CPU.

### Use it

```python
from components import handle_message

r = handle_message("the drain outside my house on nostrand ave is flooding",
                   citizen_id="CIT-000014")

r.request_type        # 'RT021'
r.label               # 'Catch basin clogged or flooding'
r.confidence          # 0.87
r.is_emergency        # False
r.ready_to_submit     # True
r.next_question       # None, or the one thing still to ask
r.needs_human         # True when it should not be guessed at
r.explain()           # every reason, in plain English
```

`handle_message` runs my part of the chain in the right order: the emergency safeguard
screens the text **before** anything is parsed or stored, and if it stops, nothing else
runs and nothing is filed.

### One component at a time

```python
from components import intake, emergency, credibility, alerting
from datetime import date

emergency.screen("I smell gas in my building").action
# 'STOP_CALL_911'

intake.parse("no heat for three days at 114 bedford ave").slots.incident_address
# '114 bedford ave'

credibility.assess("CIT-001", history=[...]).credibility_score
# 63.4

alerting.forecast("BROOKLYN", date.today(), {"wx_precip_mm": 38}).alerts
# [WARNING: Brooklyn water sewer, 46 expected against a normal 19 (x2.4)]
```

### What comes back

Every component returns a dataclass carrying `reasons` — the explanations written at the
moment each decision is made, with the weight each one contributed. That is what the
admin portal shows, and it is why removing a reason changes the outcome rather than just
changing the wording.

```python
for line in r.explain():
    print(" -", line)
# - Read as 'Catch basin clogged or flooding' (model, confidence 0.87)  (+0.87)
# - Location read from the message: address
# - First report from this person, so they start at neutral 50
```

### If something is missing

Nothing crashes. Call `health_check()` at start-up to see what loaded:

```python
from components import health_check
health_check()
# {'intake model': 'loaded', 'emergency rules': '17 patterns', ...}
```

| If this is missing | What happens |
|---|---|
| `models/c01_intent_minilm_logreg.joblib` | The bot falls back to keyword rules and says so in its reasons |
| `models/c02_emergency_tfidf_lr.joblib` | The lexicon still runs; it is the rules that carry this component |
| `data/reference/emergency_lexicon.csv` | The safeguard **fails closed**: every citizen sees the 911 advisory |
| `models/c11_demand_model.joblib` | The forecaster returns no alerts **and `ok=False`**, so silence is never mistaken for calm |

### Files

```
components/
├── __init__.py          handle_message, health_check, the four modules
├── common.py            Reason and ComponentResult, shared by all four
├── c01_nlp_bot.py       intake: intent, slots, dialogue, abstain
├── c02_emergency.py     the safeguard: lexicon, immobility patch, classifier
├── c06_credibility.py   decaying Beta, base-rate fair, recurrence escalation
└── c11_alerting.py      demand forecast and alert severity
```

They read from `models/` and `data/reference/` at the repository root, found relative to
this folder, so moving the repository is fine.

### The numbers

Every claim is in `03_Research/evidence/` with the chart and table behind it, and in
`03_Research/evidence/all_metrics.csv`. The short version:

- **C01** 0.951 macro-F1 on the team's test lines, 0.707 on the keyword-avoiding ones, 61 ms on CPU
- **C02** recall **1.000**, nothing missed, no false alarms on the hydrant and fire-escape set
- **C06** ranks citizens at Spearman 0.926 against a hidden honesty value it never sees
- **C11** error 60.6 against a 72.3 no-weather baseline, catching 91% of real storm-day spikes

To re-check them yourself:

```
py 06_Workspace_Tools/verify_components.py
```
