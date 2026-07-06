---
title: Clinic Operations Copilot
emoji: 🏥
colorFrom: blue
colorTo: indigo
sdk: docker
app_port: 8080
pinned: false
---

# Clinic Operations Copilot

A multi-agent healthcare-operations system built with **Google ADK** (Agent Development Kit) and **Gemini**. It turns a synthetic clinic data warehouse into compliance-safe executive briefs — flagging utilization spikes, no-show patterns, wait-time outliers, and staffing gaps, then projecting the impact of interventions.

Built as the capstone for Google's *5-Day AI Agents Intensive (Vibe Coding)* course.

## What it does

- **Guardrail agent** screens every request first — no individual patient data, ever (aggregates of 5+ only).
- **Ops Analyst agent** queries the warehouse, resolves date ranges, computes rates.
- **Planner agent** runs what-if simulations, always labeled `PROJECTED`.
- **Narrator agent** validates every number, compiles the brief, and appends a non-diagnostic disclaimer.
- A **monitoring loop** scans SQL directly (zero API cost) and only wakes the agents when it finds an anomaly.
- A **FastAPI** web app serves a chat UI, recent briefs, and the audit trail.

Data is 100% synthetic (`data/seed.py`) — no real patient data is used.

## Architecture

```
web/app.py ──► agents/orchestrator.py ──► guardrail (runs first)
                     │
                     ├─► ops_analyst  ──► mcp_servers/clinic_warehouse.py  (DuckDB)
                     ├─► planner      ──► mcp_servers/simulation_engine.py
                     └─► narrator     ──► mcp_servers/report_builder.py
                                          rag/brief_history.py  (past briefs)
tools/       calculator · date_resolver · output_validator
monitoring/  loop.py  (daily anomaly scan)
```

Compliance rules are enforced in code and listed in [CONSTITUTION.md](CONSTITUTION.md).

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows;  source .venv/bin/activate on macOS/Linux
pip install .
cp .env.example .env          # then paste your real GOOGLE_API_KEY into .env
python data/seed.py           # builds data/clinic.duckdb
uvicorn web.app:app --reload  # open http://localhost:8000
```

Run the tests (they mock the LLM, so no API key needed):

```bash
pytest -q
```

## Deploy to Hugging Face Spaces (free, no credit card, no laptop needed)

Hugging Face Spaces hosts the Docker container for free and gives a public URL that runs without your machine on. This repo is already configured for it (the YAML header above sets `sdk: docker` and `app_port: 8080`).

1. Create a free account at [huggingface.co](https://huggingface.co).
2. **New → Space** → choose **Docker** → **Blank** template. Give it a name.
3. In the Space, go to **Settings → Variables and secrets → New secret** and add:
   - `GOOGLE_API_KEY` = your key from [aistudio.google.com/apikey](https://aistudio.google.com/apikey)
4. Push this project to the Space's git repo (URL shown on the Space page):
   ```bash
   git init && git add . && git commit -m "Clinic Ops Copilot"
   git remote add space https://huggingface.co/spaces/YOUR_USERNAME/YOUR_SPACE_NAME
   git push space main
   ```
5. Hugging Face builds the Dockerfile automatically and serves the app at your Space URL.

The container seeds its synthetic DB at startup into `/tmp` (writable, ephemeral) — no persistent storage needed.

## Secrets & git

`.env` holds your real key and is **git-ignored** — it never gets pushed. Only `.env.example` (a template with no real values) is committed. On Hugging Face the key comes from the Space **secret** `GOOGLE_API_KEY`, injected as an environment variable — never from a committed file. This satisfies Constitution Rule 7 (no secrets in code).
