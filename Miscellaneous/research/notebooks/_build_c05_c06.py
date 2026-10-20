"""Build 03_Research/notebooks/C05_duplicate.ipynb and 03_Research/notebooks/C06_fake.ipynb."""
from pathlib import Path

import nbformat as nbf

HERE = Path(__file__).resolve().parent
md = nbf.v4.new_markdown_cell
code = nbf.v4.new_code_cell

SETUP = """import sys, pathlib
ROOT = pathlib.Path.cwd().resolve()
while not (ROOT / "01_Application" / "data" / "sample").exists():
    ROOT = ROOT.parent
sys.path.insert(0, str(ROOT / "01_Application"))
import pandas as pd
pd.set_option("display.max_columns", 40)
print("repo:", ROOT)"""


def write(name, cells):
    nb = nbf.v4.new_notebook()
    nb.cells = cells
    path = HERE / name
    path.write_text(nbf.writes(nb), encoding="utf-8")
    print("wrote", path)


write("C05_duplicate.ipynb", [
    md("# C05 Duplicate and cross-channel detection\nCivic Operations · Nabeel Taresh · Group 23"),
    md("""## 1. Goal and contract

Group reports of the same incident. Output `NEW` / `DUPLICATE` / `RECURRENCE`.
Only OPEN jobs absorb a duplicate. After closure the same place is a recurrence.
Failure returns NEW. Signature: `run(facts, candidates) -> DuplicateResult`.

Read `05_Team_Workspace/C05_C06_changelog/RESULTS.md` for the report text.
The change log is `05_Team_Workspace/C05_C06_changelog/CHANGELOG.md`."""),
    code(SETUP),
    md("""## 2. Data

Real requests from `data/sample/requests_real_sample.csv` (or processed parquet).
Hand labels: about 300 pairs, **not in git yet**. Until they arrive, the harness
uses a **weak heuristic**. Those numbers are provisional. Do not quote them as
real results.

Labelling kit: `py 06_Workspace_Tools/c05_labelling.py sample` (seed 2026)."""),
    code("""sys.path.insert(0, str(ROOT / "06_Workspace_Tools"))
from c05_labelling import load_eval_pairs, load_requests
df, origin = load_requests("auto")
print("requests", len(df), "from", origin)
pairs, kind = load_eval_pairs()
print("eval pairs", len(pairs), "kind", kind)
print(pairs["label"].value_counts())
print(pairs["distance_bin"].value_counts() if "distance_bin" in pairs.columns else "no bins")"""),
    md("""## 3. Baseline

`radius_time`: 150 m (ADDRESS) plus 48 h. `exact_key` needs BBL."""),
    md("""## 4. Proposed method

Production is `hybrid_evidence`: BBL key, radius/time, fuzzy address when pins
are weak, NetworkX group. Alternatives: DBSCAN, HDBSCAN, graph-only, fuzzy-only.
Ablation: `hybrid_no_fuzzy`."""),
    md("""## 5. Evaluation table

Same harness for every method: `py 06_Workspace_Tools/c05_c06_eval.py benchmark`.
If `label_kind` is `provisional_weak`, say so in the report."""),
    code("""p = ROOT / "03_Research" / "experiments" / "c05_benchmark.csv"
if p.exists():
    res = pd.read_csv(p)
    display(res)
else:
    print("Run: py 06_Workspace_Tools/c05_c06_eval.py benchmark")"""),
    md("""## 6. Error analysis

False merge (predicted DUPLICATE, gold not DUPLICATE) is the costly error.
Slice by `address_type` and borough in the CSV. INTERSECTION pins are noisy."""),
    md("""## 7. Decision

Production: `hybrid_evidence` at VERSION 0.1.0. Pick by false-merge first, then
F1 and ARI. Revisit when hand labels exist."""),
    md("""## 8. Artifact and version

`components/c05_duplicate.py` · VERSION 0.1.0 ·
`match_evidence` reasons · changelog in `05_Team_Workspace/C05_C06_changelog/`."""),
])

write("C06_fake.ipynb", [
    md("# C06 Fake and unfounded request risk\nCivic Operations · Nabeel Taresh · Group 23"),
    md("""## 1. Goal and contract

Score P(unfounded). Actions: PROCEED / LOWER_PRIORITY / HOLD_FOR_REVIEW.
Never REJECT. Failure: probability None, PROCEED.

Read `05_Team_Workspace/C05_C06_changelog/RESULTS.md`."""),
    code(SETUP),
    md("""## 2. Data

Label: `verified_outcome` in {GENUINE, UNFOUNDED}. Drop inconclusive.
**Never** use `resolution_description` as a feature. Reporter ids from the
shared synthetic links only. `hidden_honesty` is evaluation-only."""),
    code("""from components.c06_fake import LEAKAGE_COLUMNS, unfounded_base_rates
print("leakage columns blocked:", sorted(LEAKAGE_COLUMNS))
print("type base rates (P unfounded) sample:", list(unfounded_base_rates().items())[:5])"""),
    md("""## 3. Baseline

Rules: type base rate, burst at 5/hour, history, corroboration always lowers risk."""),
    md("""## 4. Proposed method

Production `hybrid_rules_logreg`: calibrated logistic regression plus the rule
layer. GBM is the nonlinear arm. Ablation: `logreg_no_burst`."""),
    md("""## 5. Evaluation table

`py 06_Workspace_Tools/c05_c06_eval.py benchmark` → `03_Research/experiments/c06_benchmark.csv`.
Metrics: AUC, Brier, F1 at 0.28, FPR on first-time reporters, recall on urgent
unfounded types, latency."""),
    code("""p = ROOT / "03_Research" / "experiments" / "c06_benchmark.csv"
if p.exists():
    display(pd.read_csv(p))
else:
    print("Run: py 06_Workspace_Tools/c05_c06_eval.py benchmark")"""),
    md("""## 6. Error analysis

First-time FPR is the fairness check. Urgent recall is the safety check.
NYPD “no evidence” notes inflate UNFOUNDED. Treat AUC as a proxy score."""),
    md("""## 7. Decision

Production: `hybrid_rules_logreg` at VERSION 0.1.0. Pick by Brier and first-time
FPR, with a floor on urgent recall."""),
    md("""## 8. Artifact and version

`components/c06_fake.py` · VERSION 0.1.0 ·
`contributing_factors` reasons · changelog in `05_Team_Workspace/C05_C06_changelog/`."""),
])
