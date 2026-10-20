# Evidence pack

Every experiment, chart and number behind Sankalp's four components. Generated 08 October 2026, 13:46 by `06_Workspace_Tools/build_evidence_pack.py`.

**31 items** across five folders, and **63 metrics** in `all_metrics.csv`.

Each item has an ID. Charts are PNG, the numbers behind them are CSV with the same ID.

**Start with [`00_register/`](00_register/README.md)**: what algorithms were tried, how many configurations of each, how the choice was made, and what was planned but not done.

## 01_intent · Intent

| ID | What it shows | File |
|---|---|---|
| **E01** | Which model and which training data win. MiniLM embeddings lead on every variant. | `E01_model_against_training_variant.png` |
| **E01t** | The same numbers as E01. | `E01t_model_against_training_variant.csv` |
| **E02** | Change the wording and every model drops. They learned vocabulary more than meaning. | `E02_two_test_slices.png` |
| **E03** | One run proves nothing. The spread across seeds is what the report quotes. | `E03_seed_spread.png` |
| **E04** | Where the intake bot is strong and where it is weak, type by type. | `E04_per_request_type.png` |
| **E04t** | The same numbers as E04. | `E04t_per_request_type.csv` |
| **E05** | Which request types the bot mixes up, and how often. | `E05_most_common_confusions.csv` |
| **E05b** | Every single line the shipped model gets wrong, so the errors can be read. | `E05b_every_mistake_with_its_text.csv` |
| **E06** | The rule: the best accuracy available while handing no more than one in five to a person. | `E06_abstain_threshold.png` |
| **E06t** | The same numbers as E06. | `E06t_abstain_threshold.csv` |
| **E07** | Bubble size is the model on disk. The shipped model is well inside the CPU budget. | `E07_accuracy_against_cost.png` |
| **E07t** | The same numbers as E07. | `E07t_accuracy_against_cost.csv` |
| **E24** | Span F1 for the slot filler on 308 messages whose slots were marked by hand, shown next to the same model scored on the template text it was trained on. The gap between the two bars is the reason the old 0.999 was withdrawn. | `E24_slot_span_f1.png` |
| **E24t** | The same numbers as E24, with every slot broken out separately. | `E24_slot_span_f1.csv` |

## 02_emergency · Emergency

| ID | What it shows | File |
|---|---|---|
| **E08** | Recall reaches 1.0 at 0.45. Every step further costs false alarms and buys nothing. | `E08_threshold_sweep.png` |
| **E08t** | Both lexicons, every threshold. | `E08t_threshold_sweep.csv` |
| **E09** | Nothing is missed in any category, which is the one gate that cannot be traded. | `E09_recall_by_category.png` |
| **E09t** | The same numbers as E09. | `E09t_recall_by_category.csv` |
| **E10** | The cost of recall 1.0, written out: who gets asked to confirm and why. | `E10_every_false_alarm.csv` |

## 03_credibility · Credibility

| ID | What it shows | File |
|---|---|---|
| **E11** | What matters is the ordering: dishonest reporters sit below honest ones. | `E11_score_against_hidden_honesty.png` |
| **E12** | Equally honest people should score the same whatever they report. They nearly do now. | `E12_fairness_across_report_types.png` |
| **E13** | Sewer backups are confirmed 0.8% of the time and potholes 89%. This is what the score corrects for. | `E13_confirmation_rate_by_request_type.png` |
| **E13t** | The same numbers as E13. | `E13t_confirmation_rate_by_request_type.csv` |
| **E14** | A citizen starts at 50, earns trust, takes one knock, and recovers. | `E14_recovery_after_one_bad_report.png` |

## 04_alerting · Alerting

| ID | What it shows | File |
|---|---|---|
| **E15** | Gradient boosting wins. The Poisson model is worse than using no weather at all. | `E15_forecasting_arms.png` |
| **E15t** | The same numbers as E15. | `E15t_forecasting_arms.csv` |
| **E16** | Green means we warned before the calls arrived. Red means we missed it. | `E16_storm_day_spikes.png` |
| **E16t** | Every spike checked, with the forecast. | `E16t_storm_day_spikes.csv` |

## 05_data · Data

| ID | What it shows | File |
|---|---|---|
| **E18** | The rarest types are the ones our story depends on. | `E18_class_imbalance.png` |
| **E19** | A quarter of outcomes are inconclusive and cannot be used as truth. | `E19_outcome_composition.png` |
| **E20** | It was 152 lines with two for some intents. It is now 308 with at least eight. | `E20_test_set_coverage.png` |

## Files at the top level

| File | What it is |
|---|---|
| `all_metrics.csv` | Every number quoted anywhere, with its component |
| `index.csv` | This index, as a table |

## How to regenerate

```
py 06_Workspace_Tools/verify_components.py      # re-check the gates
py 06_Workspace_Tools/build_evidence_pack.py    # rebuild every chart and table
```
