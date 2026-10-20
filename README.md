## SECTION 1 : PROJECT TITLE
## Civic Operations: Explainable City Service Triage and Field Crew Dispatch

<img src="Miscellaneous/architecture.png" style="float: left; margin-right: 0px;" />

---

## SECTION 2 : EXECUTIVE SUMMARY / PAPER ABSTRACT
New York City's 311 service receives millions of non emergency requests a year, and each one has to be read, sent to
the right agency, ranked against everything else that is open, and given to a crew. Civic Operations does this triage
with twelve intelligent reasoning components and explains every step. A resident reports a problem in plain words or
through a short form. The system stops anything that belongs with 911, works out the request type and address, routes it
to an agency, links duplicates, checks nearby schools and hospitals, weighs the reporter's track record and the risk the
report is unfounded, assigns an urgency, ranks it against every open request using official NYC service deadlines, and
plans crew routes along the real road network with a genetic algorithm and A* search. City staff approve the plan,
crews record what they found, and the outcome flows back to the reporter's record and to the resident.

It runs as four web portals (resident, city staff, crew, tech team) over one API in two Docker containers, on 150 real
NYC 311 requests. Every component sits behind an adapter with an agreed safe default, so a failure never stops the
service, and the tech team can switch any component off and measure the effect.

---

## SECTION 3 : CREDITS / PROJECT CONTRIBUTION

| Official Full Name | Student ID | Work Items (Who Did What) |
| :--- | :---: | :--- |
| Khalid Alhazmi | A0361128J | Urgency (forward chaining rules over hazard knowledge bases) and Prioritisation (weighted multi criteria ranking with ageing, the official NYC SLA table and duplicate rules), the Prioritisation guardrails (starvation floor, ageing, holds, approval only escalation), and an urgency learning experiment across four model families |
| Mohammad Alharthi | A0354097W | Agency and skill routing, External factors (OpenStreetMap proximity) and Crew deployment and route optimisation (genetic algorithm with A* routing), each with its own research report and tests, the GPU run of the language model experiments and the independent label check |
| Nabeel Taresh | A0352296X | Duplicate detection (evidence hybrid matcher), Fake request detection (calibrated rules and logistic regression) and Admin questions (natural language questions over the live queue), and the first data flow diagram |
| Indish Sankalp Sandeep | A0351383E | Intake bot, Emergency check, Credibility and escalation and Predictive alerting, the data pipeline, the component specification, the application (backend, four portals, integration of all twelve components, Docker), testing, measurement and the group report |

---

## SECTION 4 : VIDEO OF SYSTEM MODELLING & USE CASE DEMO

Links to the two videos are added here when they are uploaded.

---

## SECTION 5 : USER GUIDE

`Refer to appendix B, Installation and User Guide, in the project report at GitHub Folder: ProjectReport`

### To run the system on any computer with Docker

> git clone https://github.com/DevelopingGod/IRS-PM-2026-08-17-AIS08FT-GRP-MANTIS-CivicOperations

> cd IRS-PM-2026-08-17-AIS08FT-GRP-MANTIS-CivicOperations/SystemCode

> docker compose up --build -d

> docker compose exec backend python -m app.backend.manage import-backlog --count 150

> docker compose exec backend python -m app.backend.manage team-accounts > credentials.txt

> Go to URL using web browser: **http://localhost:8080**

`credentials.txt` holds the test IDs and PINs for city staff, crews and the tech team. Residents register with any email
on the home page. Full guide with troubleshooting: `SystemCode/SETUP.md`.

---

## SECTION 6 : PROJECT REPORT / PAPER

`Refer to project report at GitHub Folder: ProjectReport`

The report covers the business case, market research and literature review, system design and models, development and
implementation, experiments for every component and for the whole system, findings and discussion, and the appendices:
the project proposal, the system functions mapped against MR, RS and CGS, and the installation and user guide.
The team log book is in the same folder.

---

## SECTION 7 : MISCELLANEOUS

`Refer to Github Folder: Miscellaneous`

* `research/`: the notebooks, experiment result tables and evidence behind every number in the report
* `specification/`: the component specification and contracts the team agreed before building
* `architecture.png`: the system architecture
