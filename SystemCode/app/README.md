# Civic Operations app

Four portals over one reasoning pipeline, following the proposal's process (slide 5):

| Portal | Who | What they do |
|---|---|---|
| Home, Report, Track | Residents | Browse how the service works, report a problem, follow their own requests and ask the status bot about them |
| City staff | Dispatchers | The ranked queue on a map, request reasoning, the crew plan to approve, escalation approval, Admin questions, busy day alerts |
| Crew | Field crews | Only their own crew's approved jobs, in route order on the road map, and recording what they found |
| Tech team | Engineers | Component health, one switch per component that applies everywhere, measurements, accounts |

## Run it

Follow **[SETUP.md](../../SETUP.md)** at the top of the repository: clone, `docker compose up --build -d` inside `01_Application`, then load the backlog and make the team accounts. Open http://localhost:8080.

The first tech account and the starting backlog can only come from the command line, never from the web. After that the tech portal creates staff and crew accounts. A PIN is printed once.

## Accounts and privacy

* **Residents** register with an email only and receive a user ID such as `NYC-XXXX-XXXX`, which they must keep: later sign ins use the ID alone, with no password. Only a keyed hash of the email is stored, to stop one email registering twice, so the system cannot contact or identify anyone. A second registration with the same email is refused without revealing the ID.
* **Staff, crews and the tech team** sign in with their ID and a 6 digit PIN, stored as a salted PBKDF2 hash. Five wrong PINs lock the ID for 15 minutes.
* Every role check happens on the server. A resident never sees another person's request or any score. Staff never see who reported a request, only the reporter's credibility score.

## How a component plugs in

Each component has one adapter in `backend/plugins/registry.py` that renames shared fields, calls the owner's code and renames the answer back. If the code is missing, fails, returns the wrong shape or is switched off by the tech team, the safe default written in the component specification is used and every screen says so. The Intake bot and Emergency check cannot be switched off.

| Component | Owner | Today |
|---|---|---|
| Intake bot + Emergency check | Sankalp | Live, cannot be switched off |
| Agency and skill routing | Mohammad | Live, version 1.1.0 |
| Duplicate detection | Nabeel | Live |
| External factors | Mohammad | Live, version 1.0.0 |
| Credibility and escalation | Sankalp | Live, fed by real accounts |
| Fake request detection | Nabeel | Live, only a review flag counts as suspected |
| Urgency + Prioritisation | Khalid | Live, pinned to his branch |
| Crew deployment and route optimisation | Mohammad | Live, version 1.0.0: proposes routes over the whole queue, staff approve them |
| Predictive alerting | Sankalp | Live, beside the pipeline |
| Admin questions | Nabeel | Live, beside the pipeline |

Resolution time and the Profanity filter were optional in the proposal and are out of scope.

## How crews are planned

Crew deployment and route optimisation plans the whole ranked queue at once, the way Prioritisation ranks it, so it runs beside the per request pipeline (`backend/dispatch.py`).

* **Input.** Each request's place in Khalid's queue and its queue status, its skill, its coordinates and the catalog's service minutes, plus every crew with its depot. Only requests the queue marks as ready are planned. Requests waiting for review or on hold are listed, never planned.
* **A proposal only.** The plan never assigns anything. City staff approve a whole route, or pick a crew for one request, and only then does the job reach the crew.
* **Locked once approved.** An approved job is never planned again, and minutes a crew already worked today count against its day.
* **Kept fresh.** Planning takes a few seconds, so the latest plan is kept and a new one is made in the background when the queue, the approvals or the quarter hour change.
* **Safe default.** Switched off or failing, there is no plan: staff assign by hand and each crew sees its jobs in queue order.

The crews come from the shared crew table, `data/processed/synthetic_crews.parquet`, so the IDs on crew accounts stay the same everywhere.

## Starting data

The queue starts from a real backlog: the most recent 30 requests recorded in each borough of our NYC 311 sample, each run through every component once. The sample was collected in separate hours per borough, so each borough is replayed so that its newest request is a few minutes old, keeping the real spacing between requests. Every imported request is marked as a 311 replay with its original key. Address suggestions and map coordinates come from the 31,124 distinct addresses in the same sample.

## A walkthrough

1. **Home.** Scroll the story: the 3D city follows each step of the process. At the end, answer *Yes* to "Do you see a problem?".
2. **Register** with an email, save the user ID shown on the MetroCard, and continue.
3. **Report**: pick Streets and lights, then Pothole, type `37-61 79` and choose the suggestion, add a sentence, check and send. You land on Track with your reference.
4. **Track**: watch each step light up. Ask the bot "where is my request?".
5. **City staff** (footer link, staff ID and PIN): filter the queue with the headline numbers, open a request from the map or the list, read why it is where it is.
6. **Crew plan**: open a crew's proposed route, see it drawn along the real roads with arrival times, and approve it.
7. **Crew**: the crew sees its stops in that order, records "Fixed", and the reporter's record improves.
8. **Track again**: tell the bot "it is still there". It files the problem again as a recurrence.
9. **Tech team**: switch Admin questions off and see City staff told it is off. Switch it back on.

## What is measured

* **Every call:** whether the component ran live, fell back or was switched off, and how long it took.
* **Tech portal:** calls, share live, median and 90th percentile time, and errors per component.
* **Remove one run:** 34 evaluation scenarios (never filed as real requests) with everything on, then one component at a time on its safe default, counting what changes.

## Configuration

| Variable | Default | What it is |
|---|---|---|
| `CIVICOPS_SALT` | a development value | Secret for the keyed hashes. Set it for any real use |
| `CIVICOPS_DB` | `app/backend/civicops.db` | The SQLite file |
| `CIVICOPS_ORIGINS` | `http://localhost:5173` | Browser origins allowed to call the API |
| `CIVICOPS_WARMUP` | `1` | Load the language models at start up |
| `VITE_API_BASE` | empty | API address when the portals are served elsewhere |

Why it is not online yet is in `DEPLOYMENT.md`.
