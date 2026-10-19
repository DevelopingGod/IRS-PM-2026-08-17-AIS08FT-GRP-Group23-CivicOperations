# Experiment register

What was tried, how deep, and what was not. Generated 15 October 2026, 14:32.

**334 training or evaluation runs** across **27 algorithm families** and 7 components.

## What was compared

| Component | Algorithm family | What was varied | Runs | Chosen on | Outcome |
|---|---|---|---|---|---|
| C01 intake | Keyword rules (baseline) | none, it is a fixed lookup | 1 | gold macro F1 | 0.543 · the floor everything else has to beat |
| C01 intake | TF IDF with a linear SVM | 4 training data variants | 4 | gold macro F1 | 0.733 best · purely lexical, no sense of meaning |
| C01 intake | MiniLM embeddings + logistic regression | 4 data variants, then 4 encoders x 6 regularisation strengths | 28 | gold macro F1 inside the deployment budget | 0.854 · SHIPPED |
| C01 intake | DistilBERT fine tuned | 4 data variants, random search of 18 trials over 8 settings, 5 seeds | 27 | gold macro F1 | 0.811 mean · best validation score, worst gold score |
| C01 intake | RoBERTa fine tuned | 4 training data variants | 4 | gold macro F1 | 0.808 best · no better than DistilBERT for twice the size |
| C01 intake | DeBERTa v3 small | planned, never ran | 0 | n/a | skipped: sentencepiece was missing on the GPU machine |
| C01 slots | Regex patterns (the original) | none, they are fixed patterns | 1 | span F1 on messages labelled by hand | 0.434 on location and time · the floor to beat |
| C01 slots | DistilBERT word tagger, on the original labels | 2 learning rate and epoch settings | 2 | span F1 on the synthetic split | 0.999 · on template text, so it measured nothing |
| C01 slots | DistilBERT word tagger, labels repaired and gold spans hand labelled | 4 passes over 10,735 rows after repairing 2,376 truncated spans | 1 | span F1 on 308 messages labelled by hand | 0.723 on location and time, 0.984 on the synthetic split · SHIPPED |
| C02 emergency | Weighted lexicon with negation | 2 lexicons, with and without the immobility patch | 2 | recall first, then precision | recall 0.737 alone |
| C02 emergency | TF IDF with logistic regression | 8 decision thresholds against both lexicons | 16 | lowest threshold reaching recall 1.0 | recall 1.000 at 0.45 · SHIPPED |
| C06 credibility | Decaying Beta, fixed neutral prior | the original design | 1 | ranking and fairness | unfair by 4.0 points across report types |
| C06 credibility | Decaying Beta, centred on the base rate for the types reported | with per type confirmation rates | 1 | ranking and fairness | Spearman 0.926, unfairness 2.2 · SHIPPED |
| C11 alerting | Weekday and season baseline, no weather | none | 1 | error on storm days | MAE 72.3 · the honest floor |
| C11 alerting | Poisson regression with weather | one specification | 1 | error on storm days | MAE 83.1 · WORSE than using no weather |
| C11 alerting | Gradient boosting with a Poisson loss | 16 configurations over 5 hyperparameters | 17 | error on storm days | MAE 60.4, storm 73.7 · SHIPPED |
| C05 duplicate | Exact BBL + type key | none, missing BBL rows are skipped | 1 | pair F1, false-merge first | F1 0.93, false merge 0.08 on 295 hand pairs |
| C05 duplicate | Radius + time window (150 m / 48 h) | none, it is the documented baseline | 1 | pair F1, false-merge first | F1 0.69, false merge 0.48 · 150 m is too wide |
| C05 duplicate | Fuzzy address (SequenceMatcher) | strong 0.88 / weak 0.72 cutoffs | 1 | pair F1, false-merge first | F1 0.69 · only helps weak-location rows (synthetic test) |
| C05 duplicate | DBSCAN in metres + scaled hours | eps 160 m, min_samples 2 | 1 | pair F1, false-merge first | F1 0.65 |
| C05 duplicate | HDBSCAN, min_cluster_size 2 | sklearn HDBSCAN, single-linkage fallback | 1 | pair F1, false-merge first | F1 0.69 · crash on many candidates fixed |
| C05 duplicate | NetworkX connected components | pairwise edges above score 0.45 | 1 | pair F1, false-merge first | F1 0.69 · transitivity chains over-merge |
| C05 duplicate | Hybrid evidence (BBL + radius + fuzzy + graph) | radius x time x fuzzy x graph grid, then per-type radius/time | 216 | false-merge first, then F1 and ARI | SHIPPED: 25 m / 6 h + per-type table · F1 0.92 hand, 0.76 Feb silver |
| C06 fake risk | Rules (type base rate, burst, corroboration) | burst flag at 5 reports / 1 h | 1 | Brier and first-time FPR, floor on urgent recall | AUC 0.573 / Brier 0.247 on real test |
| C06 fake risk | Calibrated logistic regression | Platt sigmoid on the val split | 1 | Brier and first-time FPR | AUC 0.629 / Brier 0.236 |
| C06 fake risk | Histogram gradient boosting | depth 3, 80 iterations, then calibration | 1 | Brier and first-time FPR | AUC 0.633 / Brier 0.233 · first-time FPR 0.85 |
| C06 fake risk | Hybrid rules + logreg | 0.65 model + 0.35 rules; one burst ablation | 2 | Brier and first-time FPR, floor on urgent recall | AUC 0.613 / Brier 0.237 · SHIPPED, cold-start floor |

## Charts

| ID | What it shows |
|---|---|
| **E21** | How many runs each component got, so uneven depth is visible rather than hidden |
| **E22** | What the random search of 18 trials covered, and which hyperparameters actually moved the score |
| **E23** | Tuning the shipped model: four encoders against six regularisation strengths |
| **E24** | The slot filler scored on 308 messages labelled by hand, next to the same thing scored on the template text it was trained on, which is where the old 0.999 came from |

## What we did not do, and why

**DeBERTa v3 small never ran.** `sentencepiece` was missing on the GPU machine, so the notebook skipped it by design rather than failing. Two transformer families ran instead of three.

**The random search of 18 trials tuned the arm that lost.** The notebook hardcoded a transformer for that stage, so DistilBERT was tuned while MiniLM, which won, was not. Fixed afterwards in `06_Workspace_Tools/sweep_shipped_models.py`: 24 more runs, and the shipped setting moved from C=4.0 to C=1.0, worth 0.014 of macro F1.

**Slot filling was only ever scored on its own training text, and that is now fixed.** The old 0.999 span F1 was measured on the synthetic sentences, where the addresses and the time phrases had been inserted from a template list, so it asked the model to repeat text it had been taught. I marked the slots by hand on all 308 gold messages, repaired 2,376 training labels that had been cut short by punctuation, and measured it again. The honest number is a span F1 of 0.723 on the location and time slots, against 0.434 for the patterns that were there before. The hazard slot is no longer scored as a span at all, because where a hazard phrase begins and ends is a matter of opinion, and it was never used as anything but a keyword flag.

**Credibility was compared, not searched.** Two designs rather than a search over settings. The prior strength and the 180 day half life were chosen on reasoning, not tuned.

**Everything is scored on 308 lines.** Wide confidence intervals. Differences under about 3 points are not meaningful, and the report says so.

**C05 duplicate scores are provisional.** Scored on 295 hand-checked real pairs (AI labels under human supervision) and on NYC 311 city-marked silver pairs. One building and RT044 snow dominate the hand set, so CIs are wide.

**C06 fake risk is a proxy label.** `verified_outcome=UNFOUNDED` is a crew-note proxy, not proven fraud. Burst stayed in the shipped hybrid even though dropping it was slightly better on the small sample.

## How the choice was made

The rule was written before the numbers were seen: take the smallest model whose gold macro F1 is within two points of the best, provided it runs under 200 ms on a CPU and fits the memory budget. That rule rejects `all-mpnet-base-v2`, which scores highest at 0.874 but needs 420 MB, and selects `all-MiniLM-L6-v2` at 0.854 and 91 MB.

For the emergency safeguard the rule is different and simpler: the lowest threshold that reaches recall 1.0, then the best precision available at that recall.

C05 picks by false-merge first, then pair F1 and ARI. C06 picks by Brier and first-time FPR, with a floor on recall for high-urgency unfounded types. Neither component auto-rejects a citizen.
