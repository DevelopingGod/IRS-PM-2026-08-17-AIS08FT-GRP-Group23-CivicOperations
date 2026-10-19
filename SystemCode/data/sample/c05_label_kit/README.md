# C05 labelling kit (committed sample)

Built by `py 06_Workspace_Tools/c05_labelling.py sample` with seed **2026**.

This folder used the small committed sample (1,500 real rows), not the full
49,558. The pool had 146 pairs. We cannot reach 300 pairs until processed
data is local.

Rules: `05_Team_Workspace/C05_C06_changelog/C05-labelling-rules.md`.
Do not add a model score column. Do not use closing notes.

When the full table exists:

```
py 06_Workspace_Tools/c05_labelling.py sample --source processed --n 300 --out-dir data/processed/c05_label_kit
```

Save agreed gold labels as `c05_labelled_pairs.csv` here (or set `C05_LABELS_CSV`).
The benchmark then drops `c05_weak_eval_pairs.csv`.
