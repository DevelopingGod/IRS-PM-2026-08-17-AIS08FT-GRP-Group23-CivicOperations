# C05 labelling rules

Short rules for hand-labelling duplicate pairs. Read this before you open a sheet.

You will see two reports side by side. They share the same complaint type and the
same `sample_window_id`. There is no model score on the sheet. Do not look up a
model. Do not use the crew closing note (`resolution_description`). That note is
a later label. It would leak the answer.

## Labels (pick one)

| Label | Use when |
|---|---|
| `DUPLICATE` | Both reports describe the **same open incident**. One crew should go once. The earlier report was still OPEN when the later one was filed. |
| `RECURRENCE` | Same place and same type, but the earlier report was already **CLOSED** when the later one was filed. This is a new job. C07 will read it as a repeat. |
| `NEW` | Different incidents. Same type in the same time window is not enough. Far apart, different street, or a new problem after the first one was gone. |
| `UNSURE` | You cannot decide. Use this. Do not guess. We drop UNSURE from scoring. |

## How to decide

1. Read type, both addresses, `address_type`, distance, time gap, and both channels.
2. Check `open_closed_a`. That is the earlier report at the time of the later filing.
3. If the earlier report is CLOSED, do **not** mark DUPLICATE. Choose RECURRENCE or NEW.
4. Trust ADDRESS pins more than INTERSECTION or BLOCKFACE.
5. Different channels (PHONE vs MOBILE) can still be DUPLICATE. That is the point of C05.
6. Very close + short time + same address is often DUPLICATE. Far (hundreds of metres) is often NEW.
7. Write a short note when the case is hard.

## Worked examples

**Example 1 — DUPLICATE.**  
Catch basin flooding. 18 m apart. 40 minutes apart. Earlier still OPEN. One address
is "1017 NOSTRAND AVENUE" and the other is "1017 Nostrand Ave". Two channels.
Label: `DUPLICATE`. Note: same drain, two people.

**Example 2 — NEW.**  
Same type (pothole). 920 m apart. 2 hours apart. Different street names. Both OPEN.
Label: `NEW`. Note: same window, not the same hole.

**Example 3 — RECURRENCE.**  
Hydrant leaking. 12 m apart. Same BBL. Earlier CLOSED 3 days ago. Later report today.
Label: `RECURRENCE`. Note: same hydrant after closure.

**Example 4 — UNSURE.**  
INTERSECTION pin. 80 m apart. Addresses are two crossing streets written in different
order. Time gap 20 hours. You cannot tell if it is one ponding or two corners.
Label: `UNSURE`.

## Overlap plan

- About **50 pairs** are labelled by two people (L1 and L2). Sheets mark these as `OVERLAP`.
- The rest split across **four labellers** (L1 Nabeel, L2 Sankalp, L3 Mohammad, L4 Khalid).
- After both overlap sheets are filled, run kappa:

```
py tools/c05_labelling.py kappa --a data/sample/c05_label_kit/c05_label_sheet_L1.csv --b data/sample/c05_label_kit/c05_label_sheet_L2.csv
```

- Aim for Cohen's kappa at least 0.6. Discuss every disagreement. Write one agreed
  label into `c05_labelled_pairs.csv`.
- Do not score UNSURE. Do not score a pair the two labellers still dispute.

## Commands

On the small committed sample (what is in git today):

```
py tools/c05_labelling.py sample
```

Once the full processed table is on disk (`data/processed/requests_real.parquet`
from the OneDrive `05_Data` pack, or `py -m data.pipeline.build`):

```
py tools/c05_labelling.py sample --source processed --n 300 --out-dir data/processed/c05_label_kit
```

Seed is **2026**. Copy the finished agreed file to
`data/sample/c05_label_kit/c05_labelled_pairs.csv` (or set `C05_LABELS_CSV`).
The benchmark then uses it and drops the weak labels.
