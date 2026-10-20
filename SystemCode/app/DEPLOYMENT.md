# Deployment log

## Status on 15 October 2026: runs locally, not deployed

The demo runs on a laptop: a Python backend and a browser frontend. It was built so it can be deployed later without code changes.

## Why it was not deployed

| Option | What was checked | Result |
|---|---|---|
| Hugging Face Inference API for the models | Free accounts' hosted inference credits, checked on 15 Oct 2026 against Hugging Face's pricing and Inference Providers pages | **Not free.** Free accounts get no monthly inference credits. Calls need purchased credits or the PRO plan ($9 a month). The intake's sentence model is served, but under the sentence similarity task, not plain embeddings, so the intake model could not be swapped in unchanged |
| Our own fine tuned slot tagger on Hugging Face | Whether a custom 265 MB model can be served free | **No.** Custom models need a paid Inference Endpoint (from about $0.033 an hour, card on file) |
| Hugging Face Spaces for the whole backend | Free CPU hardware for a Python (Docker) app | **No longer free for new Spaces.** Docker and Gradio Spaces now need the PRO plan. 16 GB of RAM would have been enough for both models |
| Vercel and Supabase (the stack on presentation slide 4) | Time to set up three services and host the models | **Did not fit the 3 day build.** The models need a Python host either way, which is the part above that is no longer free |

Sources: huggingface.co/docs/inference-providers/pricing, huggingface.co/pricing, huggingface.co/docs/inference-endpoints/en/pricing, huggingface.co/docs/hub/spaces-overview. Pricing pages and forum reports disagreed in places; the stricter reading was taken.

## What is ready for deployment later

- Every setting comes from environment variables, nothing is tied to this machine:
  - `CIVICOPS_DB`, the database file path
  - `CIVICOPS_ORIGINS`, the allowed frontend addresses
  - `CIVICOPS_WARMUP`, loads the models at start up so no citizen waits
  - `VITE_API_BASE`, where the frontend finds the backend
- No paid service or API key is needed to run.
- The cheapest route found: one PRO Hugging Face Space for the backend ($9 a month), and the frontend as a free static site.

## How to run it locally

See `SETUP.md` at the top of the repository.

## Containers (added 18 Oct 2026)

`docker compose up --build` runs the app in two containers: `backend` (the API and every component, CPU only PyTorch, about 3 GB) and `web` (nginx serving the portals and forwarding `/api`, about 94 MB). Any host that runs Docker can run it unchanged. Before putting it online:

* set `CIVICOPS_SALT` to a long random secret, and `CIVICOPS_ORIGINS` to the public address;
* add a one time code by SMS or email to sign in, which needs a paid messaging service and is not built;
* move the SQLite file in the `state` volume to a managed database if more than one backend runs;
* the backend needs about 2 GB of memory for the language models.

## A live link (not set up)

The app only runs on the machine that starts it, at http://localhost:8080. A live link is a public web address anyone can open, for example for examiners or a video viewer to try it themselves. Two ways, neither set up yet:

| Way | Cost | Catch |
|---|---|---|
| A temporary tunnel from a laptop running the app (for example Cloudflare's quick tunnel) | free | works only while that laptop is on and running the app, and the address changes each time |
| A small cloud server with Docker, 4 GB of memory or more | a few dollars a month | needs a card on file, a domain or the host's address, and HTTPS |

## Decision, 19 October 2026: kept local

The backend uses about 900 MB of memory with every model loaded (measured with docker stats). Render's free and $7 plans
give 512 MB, so they cannot run it. Render's Standard plan (2 GB, about $25 a month) can, with the portals on Vercel and
`VITE_API_BASE` pointing at the backend. The team chose to keep the system local: the course asks for a runnable system
with an installation guide, and `SETUP.md` is tested on a fresh clone. For a live demo, a free Cloudflare quick tunnel
from a laptop running the app (`cloudflared tunnel --url http://localhost:8080`) gives a temporary public link.
