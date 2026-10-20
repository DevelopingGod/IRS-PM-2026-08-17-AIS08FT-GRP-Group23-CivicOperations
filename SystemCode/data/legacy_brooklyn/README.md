# Dataset

This project uses the NYC 311 Service Requests dataset, 2020 to present,
from NYC Open Data.

Source: https://data.cityofnewyork.us/Social-Services/311-Service-Requests-from-2010-to-Present/erm2-nwe9

## Current Slice

- Borough: Brooklyn
- Time window: the trailing 12 months from whenever `download_dataset.py`
  is run
- Row count when this was last pulled: 1,176,802
- Full file size: about 705MB

This is a starting point, not a final decision. The team's Sept 7 meeting
notes flag that the full dataset is too large to work with directly, and
this slice was chosen to make that concrete, not to lock in Brooklyn or 12
months as the permanent scope.

## Why the Full File Is Not in This Repository

705MB is too large for a normal GitHub commit, and even using Git LFS it
would use almost the entire free 1GB monthly bandwidth allowance on a
single clone by one person. Instead, only a small sample is committed:

- `nyc311_brooklyn_last12months_SAMPLE.csv`, the first 500 rows, so anyone
  can see the exact shape and columns of the data directly on GitHub
  without downloading anything.

## Getting the Full File Yourself

Run this once, from the repository root:

```
py -m pip install python-docx matplotlib
py 06_Workspace_Tools/legacy/download_dataset.py (legacy)
```

This writes the full CSV into this folder on your own machine. It is not
tracked by git, see `.gitignore`, so it stays local to you.

## Columns Kept

The script deliberately keeps only the columns this project's components
actually use, not the full set of columns NYC publishes.

| Column | What it is |
|---|---|
| unique_key | The request's unique ID |
| created_date | When the request was filed |
| closed_date | When the request was closed, if it has been |
| agency, agency_name | Which city agency handled it |
| complaint_type, descriptor | What kind of problem was reported |
| location_type | The kind of place the problem was reported at |
| incident_zip, incident_address, city | Where the problem is |
| status | Open, closed, in progress, and so on |
| resolution_description | The free text note written when the request is closed, this is what the outcome verification component reads |
| resolution_action_updated_date | When the resolution note was last updated |
| community_board | The local governing board for the area |
| borough | Which of the five boroughs |
| latitude, longitude | Exact coordinates, used for routing and clustering |

## Changing the Slice

Open `06_Workspace_Tools/legacy/download_dataset.py` and change `BOROUGH` or
`MONTHS_BACK` near the top, then rerun it. Whoever changes this should
also update the numbers in this README so it keeps describing the real
current slice.
