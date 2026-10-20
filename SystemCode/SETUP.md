# Set up Civic Operations on your machine

About 20 minutes the first time, most of it waiting for Docker to download.
Nothing to sign up for, no API keys, no data pack to fetch: everything the app needs is in the repository.

On a Mac or Linux, type `python3` wherever this guide says `py`.

## 1. What you need

| Tool | Why | Get it |
|---|---|---|
| Git | to download the code | https://git-scm.com |
| Docker Desktop, **running** | runs the app in two containers | https://www.docker.com/products/docker-desktop |
| Python 3.10 or newer | only to run without Docker, or to refresh Khalid's pinned copy | https://www.python.org |

Give Docker at least 4 GB of memory and keep about 6 GB of disk free.

## 2. Download the code

```bash
git clone https://github.com/DevelopingGod/IRS-Project-2026-KIMS.git
```

```bash
cd IRS-PM-2026-08-17-AIS08FT-GRP-MANTIS-CivicOperations/SystemCode
```

Every command below runs inside `SystemCode`, the folder that holds the app.

## 3. Nothing to bring in

Every component is already in the repository, Khalid's included, at the versions the app is
tested with. (Only to move to a newer version of Khalid's code does anyone run
`py -m app.backend.vendor_export`.)

## 4. Start the app

```bash
docker compose up --build -d
```

The first build takes 10 to 20 minutes and downloads about 3 GB. Later starts take seconds.

## 5. First time only: load the requests and make the accounts

Load the starting queue, 150 real NYC 311 requests run through every component (about a minute):

```bash
docker compose exec backend python -m app.backend.manage import-backlog --count 150
```

Then make the accounts for trying it out, one tech team, one city staff and six crew accounts, and save them to a file:

```bash
docker compose exec backend python -m app.backend.manage team-accounts > credentials.txt
```

Open `credentials.txt` for the user IDs and 6 digit PINs. **They are shown only this once**, and git never stores the file. The crew accounts belong to the crews Crew deployment gave the longest routes.

Want just one account of your own instead? `docker compose exec backend python -m app.backend.manage create-user --role tech --name "Your name"`, then create the others in the tech portal under **Accounts**.

## 6. Open it

Go to **http://localhost:8080**.

* **As a resident:** scroll the home page, answer *Yes* at the end, register with any email and keep the user ID it gives you.
* **As city staff, a crew or the tech team:** use the link in the footer and sign in with an ID and PIN from `credentials.txt`.
* **To see a crew at work:** as city staff open **Crew plan**, open a crew's route and approve it. Then sign in as that crew: its stops are there in that order, drawn on the roads.

`app/README.md` has a full walkthrough of the four portals.

## Everyday commands

| To | Run |
|---|---|
| Stop the app | `docker compose down` |
| Start it again | `docker compose up -d` |
| Get the latest code | `git pull`, then `docker compose up --build -d` |
| See what the backend is doing | `docker compose logs -f backend` |
| List accounts | `docker compose exec backend python -m app.backend.manage list-users` |
| Forgotten PIN | `docker compose exec backend python -m app.backend.manage reset-pin STF-XXXX-XXXX` |
| Empty every request but keep the accounts | `docker compose exec backend python -m app.backend.manage reset --yes` |
| Start completely fresh (deletes accounts too) | `docker compose down -v`, then steps 4 and 5 again |
| Make more test accounts | `docker compose exec backend python -m app.backend.manage team-accounts > credentials-2.txt` |

## If something goes wrong

| What you see | Fix |
|---|---|
| `Cannot connect to the Docker daemon` or `pipe/dockerDesktopLinuxEngine` | Docker Desktop is not running. Open it and wait until it says it is running |
| `port is already allocated` for 8080 | Something else uses that port. Stop it, or change `"8080:80"` in `docker-compose.yml` to `"8090:80"` and open http://localhost:8090 |
| `py` is not recognised | Use `python` on Windows or `python3` on a Mac |
| The page loads but shows no requests | Step 5 was skipped. Run the import command |
| The build stops with `killed` or runs out of memory | Raise Docker's memory to 4 GB or more in Docker Desktop, Settings, Resources |
| Signed in staff or crew see "That page is not part of your account" | Each account only opens its own portal. Sign out and use the right ID |

## For developers: run without Docker

Use this to change the code with live reload. You also need **Node.js 22 or newer** (https://nodejs.org).

Install once, from the project folder:

```bash
py -m pip install torch==2.14.0 --index-url https://download.pytorch.org/whl/cpu
```

On a Mac, leave out everything from `--index-url` onwards.

```bash
py -m pip install -r app/backend/requirements.txt pytest
```

```bash
npm --prefix app/frontend install
```

Then run each of these in its own terminal and keep both open:

```bash
py -m uvicorn app.backend.main:app --port 8000
```

```bash
npm --prefix app/frontend run dev
```

Open **http://localhost:5173**. The first time, run the step 5 commands with `py` in place of `docker compose exec backend python`, for example `py -m app.backend.manage import-backlog --count 150`. This setup keeps its own database in `app/backend/civicops.db`, separate from the Docker one.

### Tests

```bash
py -m pytest
```

The browser test of every portal at phone, tablet and desktop size (Windows, about 7 minutes, uses its own test database):

```bash
npx --prefix app/frontend playwright install chromium
```

```bash
npx --prefix app/frontend playwright test --config app/frontend/playwright.config.js
```

## Before putting it online

The defaults are for running on your own machine. Before anyone else uses it, set `CIVICOPS_SALT` to a long random secret (in a `.env` file next to `docker-compose.yml`) and read `app/DEPLOYMENT.md`.
