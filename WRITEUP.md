# Clinic Operations Copilot — Project Writeup

**Live demo:** https://huggingface.co/spaces/tanaygattani/clinical-ops-copilot
**Track:** Agents for Business — auditable operational intelligence for a clinic network, where business impact and trust both matter.
**Event:** Google 5-Day AI Agents Intensive (Vibe Coding) — capstone

---

## The problem

A regional operations manager is responsible for a dozen clinics. Somewhere in the
network, right now, a clinic's no-show rate is creeping up, a provider's wait times
are drifting past the acceptable band, and a location is chronically over- or
under-utilized. **The data to see all of this already exists** — thousands of daily
metric rows sit in a warehouse.

The problem is not the query. It's everything around it:

- Turning raw numbers into a **decision-ready brief** a busy manager will actually read.
- Doing it **without ever leaking individual patient data** — healthcare operations
  live under real privacy constraints.
- Doing it **without hallucinating figures**. An LLM that writes "no-shows rose to 22%"
  when the real number is 14% is worse than useless — it's dangerous.

This is an important problem because the failure mode of naive "LLM + dashboard"
tools is silent: they produce fluent, confident, wrong prose, and a human has no way
to know which numbers to trust.

## Why agents

A single prompt can't solve this well, because it's not one job — it's five jobs with
conflicting constraints:

1. **Screen** the request (safety/compliance — must run first, must be able to refuse).
2. **Pull and compute** the metrics (deterministic SQL, date-range resolution).
3. **Simulate** an intervention (what-if math, must never be confused with actual data).
4. **Write** the brief (fluent natural language — the one job an LLM is actually good at).
5. **Verify** every number before it ships (adversarial check against ground truth).

Bundling these into one agent means the same component that *writes* a number also
*grades* it — which is exactly the setup that produces confident hallucinations. So the
architecture makes them **separate agents**, and the key design decision follows from
that separation: **the generator is never the evaluator.** The narrator writes the
brief; a completely independent, deterministic groundedness check re-derives every
figure from SQL and scores it. The agents don't just divide labor — they hold each
other accountable.

## Solution & architecture

![Architecture](docs/architecture.svg)

Every request flows through a **trust layer**:

- **Guardrail** (runs first, no exceptions) — refuses anything that would expose
  individual records; only aggregates of 5+ pass.
- **Orchestrator** (Google ADK) — routes work to the specialists.
- **ops_analyst → clinic_warehouse (MCP)** — DuckDB queries, always aggregated. Chat
  questions reach this through the orchestrator; briefs call the warehouse's
  `brief_metrics` tool directly, so the figures exist before the model is invoked.
- **planner → simulation_engine (MCP)** — what-if projections, always labeled `PROJECTED`.
- **narrator → report_builder (MCP)** — compiles the brief, pulls prior briefs from a
  small RAG store for continuity, appends a non-diagnostic disclaimer.
- **Groundedness evaluator** — deterministic (regex-extracts every figure, re-derives it
  from SQL, scores within tolerance). No second LLM call, so it can't hallucinate its way
  to a passing grade.
- **Audit log** (`audit.jsonl`) — append-only record of every agent action.

The three tool servers are exposed over the **Model Context Protocol** (FastMCP, stdio),
so the agents' capabilities are clean, inspectable tools rather than buried function calls.

Governance is not a promise in a README — it's a written [CONSTITUTION.md](CONSTITUTION.md)
of 8 rules enforced at runtime (guardrail-first, aggregates-only, PROJECTED labels,
generator≠evaluator, audit-everything, secrets-in-env-only, etc.).

## What makes it trustworthy (the differentiators)

Most demo agents ask you to trust them. This one is built to be *checked*:

- **Live agent trace** — each brief carries the actual sequence of steps that produced it,
  naming the real component at each stage (guardrail verdict, the MCP tool that returned
  the figures, whether the model or the deterministic fallback wrote the prose, the
  groundedness result). It reports what ran, not a fixed script.
- **Groundedness score** — every brief ships with a score and a per-figure verified/
  unverified breakdown, computed independently of the writer.
- **Interactive simulator** — test a staffing, scheduling, or no-show intervention and see
  the `PROJECTED` impact before committing budget.

## The build

- **Google ADK** — agents, `before_agent_callback` guardrail, sub-agent orchestration,
  LiteLLM adapter for the model.
- **Model Context Protocol** — 3 FastMCP tool servers (warehouse, simulator, report builder)
  over stdio.
- **DuckDB** — a 7-year (2019–2025) synthetic warehouse across 10 clinics with per-clinic
  profiles, seasonality, YoY growth, a 2020 COVID shock, and injected anomalies. Seeded via
  a bulk CSV `COPY` load for speed.
- **Groq (Llama 3.3 70B)** via LiteLLM — LLM inference on a free tier, no credit card
  (Gemini is a drop-in fallback via `LLM_PROVIDER`).
- **FastAPI + vanilla JS + Chart.js** — the product UI, no build step. Filter by clinic,
  year/quarter, or custom date range.
- **Hugging Face Spaces (Docker)** — deployed 24/7, free, no card. The container re-seeds
  its synthetic DB into `/tmp` at startup, so no persistent storage or secrets on disk.

**Total cost to build and run: $0.**

## Journey & decisions

- **Started** as a single "analyst" agent, then split it once the hallucination problem
  became obvious — the generator/evaluator separation is the design's spine.
- **Deterministic evaluator over an LLM judge:** an LLM grading an LLM can be gamed and
  costs tokens; regex + SQL truth is cheap, reproducible, and can't be sweet-talked.
- **Groq over paid APIs:** the constraint was zero cost with no credit card, which ruled
  out several "free tier" options that still require billing on file.
- **Made the agents visible:** the biggest leap in credibility wasn't a new agent — it was
  surfacing the trace and groundedness score so a judge (or a manager) can watch the system
  check itself.

## Reproducing

Setup and deployment steps are in the [README](README.md). Tests mock the LLM, so they run
with no API key: `pytest -q`. No secrets are committed — real keys live only in a
git-ignored `.env` locally and as a Hugging Face Space secret in production.
