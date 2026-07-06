# Clinic Operations Copilot — Implementation Plan

> **Status**: AWAITING REVIEW — Do not implement until approved.

This plan covers every component in [GOAL.md](file:///c:/Users/Tanay/Desktop/Clinical_Ops/GOAL.md).
All rules in [CONSTITUTION.md](file:///c:/Users/Tanay/Desktop/Clinical_Ops/CONSTITUTION.md) are baked into the design.
Build order follows [SKILL.md](file:///c:/Users/Tanay/Desktop/Clinical_Ops/SKILL.md) exactly.
Progress will be tracked in [STATE.md](file:///c:/Users/Tanay/Desktop/Clinical_Ops/STATE.md).

---

## Directory Structure

```
Clinical_Ops/
├── GOAL.md
├── CONSTITUTION.md
├── SKILL.md
├── STATE.md
├── PLAN.md                          ← this file
├── pyproject.toml                   ← project deps & metadata
├── .env.example                     ← env var template (no real secrets)
├── Dockerfile
├── data/
│   └── seed.py                      ← synthetic DuckDB data generator
├── mcp_servers/
│   ├── clinic_warehouse.py          ← MCP server 1
│   ├── simulation_engine.py         ← MCP server 2
│   └── report_builder.py            ← MCP server 3
├── tools/
│   ├── calculator.py                ← Tool 1
│   ├── date_resolver.py             ← Tool 2
│   └── output_validator.py          ← Tool 3
├── rag/
│   ├── metrics_catalog.yaml         ← RAG source 1
│   └── brief_history.py             ← RAG retriever 2
├── agents/
│   ├── guardrail.py                 ← Agent 1
│   ├── ops_analyst.py               ← Agent 2
│   ├── planner.py                   ← Agent 3
│   ├── narrator.py                  ← Agent 4
│   └── orchestrator.py              ← Root agent + wiring
├── monitoring/
│   └── loop.py                      ← Daily monitoring loop
├── web/
│   └── app.py                       ← FastAPI web interface
├── logs/
│   └── audit.jsonl                  ← Audit trail (auto-created)
└── tests/
    ├── test_data.py
    ├── test_mcp_clinic_warehouse.py
    ├── test_mcp_simulation_engine.py
    ├── test_mcp_report_builder.py
    ├── test_tool_calculator.py
    ├── test_tool_date_resolver.py
    ├── test_tool_output_validator.py
    ├── test_rag_metrics_catalog.py
    ├── test_rag_brief_history.py
    ├── test_agent_guardrail.py
    ├── test_agent_ops_analyst.py
    ├── test_agent_planner.py
    ├── test_agent_narrator.py
    ├── test_agent_orchestrator.py
    ├── test_monitoring_loop.py
    └── test_e2e.py
```

---

## Open Questions

> [!IMPORTANT]
> Please confirm or adjust these before I proceed.

1. **LLM model**: Plan assumes `gemini-2.0-flash` for all agents. Want a different model?
2. **DuckDB location**: Plan stores database at `data/clinic.duckdb` (file-local). OK?
3. **Cloud Run project**: Need your GCP project ID and region for deployment, or should I leave those as env vars for now?
4. **MCP transport**: Plan uses **stdio** transport for local dev (ADK spawns MCP servers as subprocesses). SSE for Cloud Run. Acceptable?

---

## Phase 0 — Project Bootstrap

### [NEW] `pyproject.toml`

Defines project metadata and dependencies. To respect Ponytail rules, we minimize external libraries by using Python's standard library for YAML-free JSON parsing, custom `.env` loading, and async scheduling.

```
Dependencies:
  google-adk >= 1.2.0
  duckdb >= 1.3.0
  mcp[cli] >= 1.9.0
  fastapi >= 0.115.0
  uvicorn >= 0.34.0
  httpx >= 0.28.0
  pytest >= 8.0
  pytest-asyncio >= 0.25.0

Dev dependencies:
  ruff
```

### [NEW] `.env.example`

```
GOOGLE_API_KEY=your-key-here
CLINIC_DB_PATH=data/clinic.duckdb
LOG_PATH=logs/audit.jsonl
```

> [!IMPORTANT]
> **CONSTITUTION Rule 7**: No secrets in code. Every secret comes from env vars.
> **Ponytail Rule**: Loader function is written in stdlib (under 10 lines) rather than introducing `python-dotenv`.


**Test**: `pip install -e .` completes without error.
**PASS**: All packages installed, `python -c "import google.adk; import duckdb; import mcp"` exits 0.

---

## Phase 1 — Synthetic Data

### [NEW] `data/seed.py`

Generates a DuckDB database with synthetic clinic operations data.

#### Required Injected Patterns (verified in test_data.py)
- **CLINIC_01**: Tuesday utilization consistently 115-125% (anomaly to detect capacity bottleneck)
- **CLINIC_03**: Monday no_show_rate consistently 38-42% (anomaly to detect access bottleneck)
- **PROVIDER_07**: avg_wait_minutes consistently 20+ above clinic average
- **CLINIC_05**: afternoon slot utilization consistently below 70%

#### Tables

| Table | Columns | Rows |
|---|---|---|
| `daily_metrics` | `date DATE, clinic_id TEXT, metric_name TEXT, metric_value FLOAT` | ~3,650 (10 clinics × 365 days) |
| `appointments` | `appt_id TEXT, date DATE, clinic_id TEXT, provider_id TEXT, status TEXT, wait_minutes INT` | ~50,000 |
| `staffing` | `date DATE, clinic_id TEXT, role TEXT, headcount INT, fte FLOAT` | ~7,300 |
| `patient_satisfaction` | `survey_id TEXT, date DATE, clinic_id TEXT, score FLOAT, category TEXT` | ~10,000 |

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `create_database(db_path: str) -> None` | Path to `.duckdb` file | Creates file on disk | Creates schema + populates all tables |
| `_generate_daily_metrics(con) -> None` | DuckDB connection | Inserts rows | Metrics: no_show_rate, avg_wait, utilization, revenue_per_visit |
| `_generate_appointments(con) -> None` | DuckDB connection | Inserts rows | Statuses: completed, no_show, cancelled, in_progress |
| `_generate_staffing(con) -> None` | DuckDB connection | Inserts rows | Roles: physician, nurse, admin, ma |
| `_generate_satisfaction(con) -> None` | DuckDB connection | Inserts rows | Categories: overall, wait_time, provider, facility |

> [!NOTE]
> **CONSTITUTION Rule 2**: All data is synthetic. No real patient data. All queries will enforce aggregate minimums of 5.

#### Connections
- Consumed by: `mcp_servers/clinic_warehouse.py`
- No upstream dependency.

#### Test — `tests/test_data.py`
```
1. Call create_database("data/test_clinic.duckdb")
2. Connect and run: SELECT COUNT(*) FROM daily_metrics   → > 0
3. Connect and run: SELECT COUNT(*) FROM appointments    → > 0
4. Connect and run: SELECT COUNT(*) FROM staffing        → > 0
5. Connect and run: SELECT COUNT(*) FROM patient_satisfaction → > 0
6. Verify 10 distinct clinic_ids exist
7. SELECT AVG(metric_value) FROM daily_metrics 
   WHERE clinic_id='CLINIC_01' AND metric_name='utilization' 
   AND strftime('%w', date)='2' → result between 1.10 and 1.30
8. SELECT AVG(metric_value) FROM daily_metrics 
   WHERE clinic_id='CLINIC_03' AND metric_name='no_show_rate' 
   AND strftime('%w', date)='1' → result between 0.35 and 0.45
9. Clean up test db file
```
**PASS**: All 8 assertions pass. Test DB deleted.


---

## Phase 2 — MCP Servers

### MCP Server 1: [NEW] `mcp_servers/clinic_warehouse.py`

**Purpose**: Exposes the DuckDB warehouse as queryable tools over MCP.

#### MCP Tools Exposed

| Tool Name | Inputs | Output | Purpose |
|---|---|---|---|
| `query_metric` | `metric_name: str, clinic_id: str, start_date: str, end_date: str` | `dict {metric_name, clinic_id, start_date, end_date, values: [{date, value}]}` | Retrieve a single metric time-series |
| `compare_clinics` | `metric_name: str, clinic_ids: list[str], date: str` | `dict {metric_name, date, comparisons: [{clinic_id, value}]}` | Side-by-side comparison of clinics |
| `summary_stats` | `metric_name: str, start_date: str, end_date: str` | `dict {metric_name, period, mean, median, min, max, stddev, n}` | Aggregate stats across all clinics |
| `appointment_volume` | `clinic_id: str, start_date: str, end_date: str, group_by: str` | `dict {clinic_id, period, groups: [{group_key, count, pct}]}` | Appointment counts grouped by status/provider |
| `staffing_snapshot` | `clinic_id: str, date: str` | `dict {clinic_id, date, staff: [{role, headcount, fte}]}` | Current staffing at a clinic |

#### Internal Functions

| Function | Inputs | Outputs |
|---|---|---|
| `_get_connection() -> duckdb.DuckDBPyConnection` | (reads `CLINIC_DB_PATH` env var) | DuckDB connection |
| `_enforce_minimum_n(result: dict, min_n: int = 5) -> dict` | Query result | Raises `ValueError` if aggregation covers < 5 entities |

> [!IMPORTANT]
> **CONSTITUTION Rule 2**: `_enforce_minimum_n` is called inside every tool. If fewer than 5 records back the aggregate, the tool returns an error message instead of data.

#### Connections
- Depends on: `data/clinic.duckdb` (from Phase 1)
- Consumed by: `agents/ops_analyst.py` (via `McpToolset` + `StdioServerParams`)

#### Transport
- Local: `mcp.run(transport="stdio")`
- Cloud Run: `mcp.run(transport="sse", host="0.0.0.0", port=8081)`

#### Test — `tests/test_mcp_clinic_warehouse.py`
```
1. Seed test DB via seed.py
2. Start MCP server via StdioServerParams
3. Call query_metric("no_show_rate", "CLINIC_01", "2025-01-01", "2025-01-31")
   → returns dict with ≥ 1 value entry
4. Call compare_clinics("avg_wait", ["CLINIC_01","CLINIC_02"], "2025-06-15")
   → returns 2 comparisons
5. Call summary_stats("utilization", "2025-01-01", "2025-12-31")
   → returns dict with keys mean, median, min, max, n; n ≥ 5
6. Call appointment_volume("CLINIC_01", "2025-01-01", "2025-03-31", "status")
   → returns groups list with ≥ 1 entry
7. Call staffing_snapshot("CLINIC_01", "2025-06-15")
   → returns staff list with ≥ 1 role
8. Shut down server via exit_stack
```
**PASS**: All 5 tool calls return valid dicts, no exceptions, `n ≥ 5` on aggregates.

---

### MCP Server 2: [NEW] `mcp_servers/simulation_engine.py`

**Purpose**: Runs what-if operational simulations (e.g., "what if we add a provider?").

#### MCP Tools Exposed

| Tool Name | Inputs | Output | Purpose |
|---|---|---|---|
| `simulate_staffing_change` | `clinic_id: str, role: str, delta: int, horizon_days: int` | `dict {clinic_id, scenario, projected_metrics: {wait_time, utilization, throughput}, confidence_interval, label: "PROJECTED"}` | Model effect of adding/removing staff |
| `simulate_schedule_change` | `clinic_id: str, slot_duration_minutes: int, slots_per_day: int, horizon_days: int` | `dict {clinic_id, scenario, projected_metrics: {daily_capacity, wait_time, utilization}, label: "PROJECTED"}` | Model effect of changing appointment slots |
| `simulate_noshow_intervention` | `clinic_id: str, intervention: str, expected_reduction_pct: float, horizon_days: int` | `dict {clinic_id, intervention, projected_metrics: {no_show_rate, revenue_impact, slot_utilization}, label: "PROJECTED"}` | Model effect of no-show reduction strategies |

> [!WARNING]
> **CONSTITUTION Rule 5**: Every result dict includes `label: "PROJECTED"`. The narrator agent must surface this label in output.

#### Internal Functions

| Function | Inputs | Outputs |
|---|---|---|
| `_load_baseline(clinic_id: str) -> dict` | clinic_id | Baseline metrics from DuckDB |
| `_run_model(baseline: dict, changes: dict) -> dict` | Baseline + proposed deltas | Projected metrics (simple linear/ratio model) |

#### Connections
- Depends on: `data/clinic.duckdb` (reads baseline metrics)
- Consumed by: `agents/planner.py` (via `McpToolset` + `StdioServerParams`)

#### Test — `tests/test_mcp_simulation_engine.py`
```
1. Seed test DB
2. Start MCP server via StdioServerParams
3. Call simulate_staffing_change("CLINIC_01", "physician", 1, 90)
   → returns dict with "PROJECTED" label and projected_metrics keys
4. Call simulate_schedule_change("CLINIC_01", 15, 40, 90)
   → returns dict with "PROJECTED" label
5. Call simulate_noshow_intervention("CLINIC_01", "sms_reminders", 0.15, 90)
   → returns dict with "PROJECTED" label and revenue_impact
6. Verify ALL results contain label == "PROJECTED"
7. Shut down server
```
**PASS**: All 3 tools return results with `label == "PROJECTED"`, no exceptions.

---

### MCP Server 3: [NEW] `mcp_servers/report_builder.py`

**Purpose**: Formats validated data into structured report sections.

#### MCP Tools Exposed

| Tool Name | Inputs | Output | Purpose |
|---|---|---|---|
| `build_metric_section` | `title: str, data: dict, commentary: str` | `dict {section_html: str, section_markdown: str}` | Render a single metric section |
| `build_executive_brief` | `title: str, date: str, sections: list[dict], disclaimer: str` | `dict {brief_markdown: str, brief_html: str, metadata: {generated_at, section_count}}` | Assemble full executive brief |
| `build_comparison_table` | `title: str, rows: list[dict], columns: list[str]` | `dict {table_markdown: str, table_html: str}` | Render a comparison table |

> [!IMPORTANT]
> **CONSTITUTION Rule 3**: `build_executive_brief` ALWAYS injects the non-diagnostic disclaimer: *"This report is for operational decision support only. It does not constitute medical diagnosis, treatment recommendation, or clinical advice."*

#### Internal Functions

| Function | Inputs | Outputs |
|---|---|---|
| `_render_markdown_section(title: str, data: dict, commentary: str) -> str` | Section data | Formatted markdown string |
| `_render_html_section(title: str, data: dict, commentary: str) -> str` | Section data | Formatted HTML string |
| `_inject_disclaimer(content: str, disclaimer: str) -> str` | Report content | Content with disclaimer prepended |

#### Connections
- No database dependency.
- Consumed by: `agents/narrator.py` (via `McpToolset` + `StdioServerParams`)

#### Test — `tests/test_mcp_report_builder.py`
```
1. Start MCP server via StdioServerParams
2. Call build_metric_section("No-Show Rate", {"mean": 0.12, "trend": "declining"}, "Rates improved.")
   → returns dict with non-empty section_markdown
3. Call build_executive_brief("Weekly Brief", "2025-06-28", [section_from_step_2], "")
   → returns dict with brief_markdown containing the disclaimer text
4. Call build_comparison_table("Clinic Comparison", [{"clinic":"C1","wait":12},{"clinic":"C2","wait":18}], ["clinic","wait"])
   → returns dict with table_markdown containing "C1" and "C2"
5. Verify disclaimer string appears in brief_markdown from step 3
6. Shut down server
```
**PASS**: All 3 tools return valid output, disclaimer present in every brief.

---

## Phase 3 — Tools

### Tool 1: [NEW] `tools/calculator.py`

**Purpose**: Performs validated arithmetic for clinical metrics (percentages, rates, per-unit calculations).

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `calculate(expression: str, context: str) -> dict` | `expression`: math expression string; `context`: what this computes | `dict {expression, result: float, context, validated: bool}` | Evaluates arithmetic safely (no eval, uses `ast.literal_eval` or manual parser) |
| `percentage_change(old_value: float, new_value: float) -> dict` | Two floats | `dict {old_value, new_value, change_pct: float, direction: str}` | Compute % change |
| `rate_per_unit(numerator: float, denominator: float, unit: str) -> dict` | Num, denom, unit label | `dict {numerator, denominator, unit, rate: float}` | Compute rate |

> [!NOTE]
> Exposed to ADK as `FunctionTool` instances.

#### Connections
- Consumed by: `agents/ops_analyst.py`, `agents/planner.py`

#### Test — `tests/test_tool_calculator.py`
```
1. calculate("100 * 0.15", "no-show cost") → result == 15.0
2. percentage_change(100, 85) → change_pct == -15.0, direction == "decrease"
3. percentage_change(80, 100) → change_pct == 25.0, direction == "increase"
4. rate_per_unit(500, 40, "visits/provider") → rate == 12.5
5. calculate("1/0", "div-by-zero") → returns error dict, no exception
```
**PASS**: All 5 assertions correct, no unhandled exceptions.

---

### Tool 2: [NEW] `tools/date_resolver.py`

**Purpose**: Resolves natural-language date references into ISO date ranges.

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `resolve_date_range(reference: str, anchor_date: str = "") -> dict` | `reference`: e.g., "last week", "Q2 2025", "past 30 days"; `anchor_date`: optional ISO date | `dict {reference, start_date: str, end_date: str, anchor: str}` | Convert text to date range |
| `get_comparison_periods(reference: str, anchor_date: str = "") -> dict` | Same as above | `dict {current: {start, end}, previous: {start, end}}` | Get current + prior period for comparison |

#### Connections
- Consumed by: `agents/ops_analyst.py`, `agents/planner.py`

#### Test — `tests/test_tool_date_resolver.py`
```
1. resolve_date_range("last week", "2025-06-28")
   → start_date == "2025-06-16", end_date == "2025-06-22" (Mon-Sun of prior week)
2. resolve_date_range("Q2 2025")
   → start_date == "2025-04-01", end_date == "2025-06-30"
3. resolve_date_range("past 30 days", "2025-06-28")
   → start_date == "2025-05-29", end_date == "2025-06-28"
4. get_comparison_periods("last month", "2025-06-28")
   → current.start == "2025-05-01", previous.start == "2025-04-01"
5. resolve_date_range("invalid_gibberish")
   → returns error dict with message, no exception
```
**PASS**: All 5 assertions correct.

---

### Tool 3: [NEW] `tools/output_validator.py`

**Purpose**: Validates numbers and facts before they enter any output brief.

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `validate_metric(name: str, value: float, expected_range: tuple[float, float]) -> dict` | Metric name, value, (min, max) | `dict {name, value, valid: bool, reason: str}` | Check value is within plausible range |
| `validate_brief_section(section: dict) -> dict` | Section dict with data | `dict {valid: bool, issues: list[str], section_title: str}` | Check a report section for issues |
| `validate_all(sections: list[dict]) -> dict` | List of sections | `dict {all_valid: bool, total_sections: int, failed_sections: list[str], details: list[dict]}` | Batch validate entire brief |

> [!IMPORTANT]
> **CONSTITUTION Rule 4**: This tool runs before any number enters a brief. The narrator agent calls `validate_all` before passing sections to `report_builder`.

> [!IMPORTANT]
> **CONSTITUTION Rule 8**: This validator (evaluator) is always a separate tool from the agent that generated the data (generator). The ops_analyst generates; the output_validator evaluates.

#### Known Ranges (built-in)

| Metric | Min | Max |
|---|---|---|
| `no_show_rate` | 0.0 | 1.0 |
| `avg_wait` | 0.0 | 300.0 (minutes) |
| `utilization` | 0.0 | 1.0 |
| `revenue_per_visit` | 0.0 | 10000.0 |
| `satisfaction_score` | 0.0 | 5.0 |

#### Connections
- Consumed by: `agents/narrator.py` (before building the report)

#### Test — `tests/test_tool_output_validator.py`
```
1. validate_metric("no_show_rate", 0.12, (0.0, 1.0)) → valid == True
2. validate_metric("no_show_rate", 1.5, (0.0, 1.0)) → valid == False, reason contains "out of range"
3. validate_metric("avg_wait", -5, (0.0, 300.0)) → valid == False
4. validate_brief_section({"title": "Wait Times", "data": {"avg_wait": 25}})
   → valid == True
5. validate_all([valid_section, invalid_section])
   → all_valid == False, failed_sections has 1 entry
```
**PASS**: All 5 assertions correct.

---

## Phase 4 — RAG Retrievers

### RAG Source 1: [NEW] `rag/metrics_catalog.json`

**Purpose**: A structured catalog of every metric the system knows about, used as context for agents.

#### Schema

```json
{
  "metrics": [
    {
      "name": "no_show_rate",
      "display_name": "No-Show Rate",
      "description": "Proportion of scheduled appointments where patient did not arrive",
      "unit": "ratio",
      "valid_range": [0.0, 1.0],
      "direction": "lower_is_better",
      "category": "access",
      "related_metrics": ["utilization", "revenue_per_visit"]
    },
    {
      "name": "avg_wait",
      "display_name": "Average Wait Time",
      "description": "Mean minutes from check-in to provider contact",
      "unit": "minutes",
      "valid_range": [0.0, 300.0],
      "direction": "lower_is_better",
      "category": "efficiency",
      "related_metrics": ["patient_satisfaction", "utilization"]
    },
    {
      "name": "utilization",
      "display_name": "Provider Utilization",
      "description": "Proportion of available appointment slots filled",
      "unit": "ratio",
      "valid_range": [0.0, 1.0],
      "direction": "higher_is_better",
      "category": "efficiency",
      "related_metrics": ["staffing", "avg_wait"]
    },
    {
      "name": "revenue_per_visit",
      "display_name": "Revenue Per Visit",
      "description": "Average revenue generated per completed appointment",
      "unit": "USD",
      "valid_range": [0.0, 10000.0],
      "direction": "higher_is_better",
      "category": "financial",
      "related_metrics": ["utilization", "no_show_rate"]
    },
    {
      "name": "patient_satisfaction",
      "display_name": "Patient Satisfaction Score",
      "description": "Mean survey score across all categories",
      "unit": "score (1-5)",
      "valid_range": [0.0, 5.0],
      "direction": "higher_is_better",
      "category": "quality",
      "related_metrics": ["avg_wait", "staffing"]
    }
  ]
}
```

#### Retriever Function (in `rag/brief_history.py` or inline)

| Function | Inputs | Outputs |
|---|---|---|
| `load_metrics_catalog() -> dict` | (reads `rag/metrics_catalog.json`) | Parsed JSON dict using stdlib `json.load()` |
| `lookup_metric(name: str) -> dict` | Metric name | Single metric entry or error |
| `get_metrics_by_category(category: str) -> list[dict]` | Category name | List of matching metrics |

#### Connections
- Consumed by: `agents/ops_analyst.py` (as context in instruction), `tools/output_validator.py` (for valid ranges)

#### Test — `tests/test_rag_metrics_catalog.py`
```
1. load_metrics_catalog() → returns dict with "metrics" key, ≥ 5 entries
2. lookup_metric("no_show_rate") → returns dict with name == "no_show_rate"
3. lookup_metric("nonexistent") → returns None or error dict
4. get_metrics_by_category("efficiency") → returns ≥ 2 entries
```
**PASS**: All 4 assertions correct.


---

### RAG Source 2: [NEW] `rag/brief_history.py`

**Purpose**: Stores and retrieves past executive briefs for comparison and trending.

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `store_brief(date: str, brief_markdown: str, metadata: dict) -> None` | Date, brief content, metadata | Writes to DuckDB `brief_history` table | Save generated brief |
| `retrieve_latest(n: int = 5) -> list[dict]` | Number of briefs | `list[{date, brief_markdown, metadata}]` | Get N most recent briefs |
| `retrieve_by_date(date: str) -> dict` | ISO date | `{date, brief_markdown, metadata}` or None | Get specific brief |
| `search_briefs(query: str, n: int = 3) -> list[dict]` | Search text, count | List of briefs with matching content | Simple text search in past briefs |

#### Storage
- Table `brief_history` in `data/clinic.duckdb`:
  - `date DATE, brief_markdown TEXT, metadata JSON, created_at TIMESTAMP`

#### Connections
- Depends on: `data/clinic.duckdb`
- Consumed by: `agents/narrator.py` (for historical context)

#### Test — `tests/test_rag_brief_history.py`
```
1. store_brief("2025-06-01", "# Test Brief\nContent here.", {"version": 1})
   → no exception
2. retrieve_latest(1) → returns list with 1 entry, date == "2025-06-01"
3. retrieve_by_date("2025-06-01") → returns dict with brief_markdown containing "Test Brief"
4. retrieve_by_date("1999-01-01") → returns None
5. search_briefs("Content here") → returns ≥ 1 result
```
**PASS**: All 5 assertions correct.

---

## Phase 5 — Agents

> [!IMPORTANT]
> **CONSTITUTION Rule 6**: Every agent action writes to `/logs/audit.jsonl`. This is implemented via a shared `_audit_log(agent_name, action, details)` utility called in every callback.

### Shared Utility: [NEW] `agents/_audit.py`

| Function | Inputs | Outputs |
|---|---|---|
| `audit_log(agent_name: str, action: str, details: dict) -> None` | Agent name, action type, detail dict | Appends JSON line to `LOG_PATH` |
| `read_audit_log(n: int = 50) -> list[dict]` | Number of entries | Last N audit entries |

Log entry format:
```json
{"timestamp": "ISO8601", "agent": "guardrail", "action": "request_screened", "details": {"allowed": true, "reason": "..."}}
```

---

### Agent 1: [NEW] `agents/guardrail.py`

**Purpose**: Screens every incoming request FIRST. Blocks requests for individual patient data or clinical diagnoses.

> [!CAUTION]
> **CONSTITUTION Rule 1**: Guardrail runs first on every request. No exceptions.

#### Implementation

| Export | Type | Purpose |
|---|---|---|
| `guardrail_agent` | `Agent` | The guardrail LLM agent |
| `guardrail_callback(callback_context) -> Optional[Content]` | Function | `before_agent_callback` for the root orchestrator |

#### `guardrail_callback` Logic
1. Extract user message from `callback_context.user_content`
2. Pass message to `guardrail_agent` for classification
3. If the request asks for individual patient data → return blocking `Content` with explanation
4. If the request asks for clinical diagnosis → return blocking `Content`
5. Otherwise → return `None` (allow through)
6. Audit log every decision

#### Agent Instruction (summary)
```
You are a healthcare compliance guardrail. Classify the incoming request:
- BLOCK if it asks for data about fewer than 5 patients
- BLOCK if it requests clinical diagnosis or treatment recommendations
- ALLOW if it asks for operational metrics, aggregates, simulations, or reports
Return JSON: {"decision": "ALLOW"|"BLOCK", "reason": "..."}
```

#### Connections
- Consumed by: `agents/orchestrator.py` (as `before_agent_callback`)
- No tool dependencies.

#### Test — `tests/test_agent_guardrail.py`
```
1. Input: "What is patient John Smith's diagnosis?"
   → decision == "BLOCK", audit log entry written
2. Input: "What is the average wait time across all clinics?"
   → decision == "ALLOW", audit log entry written
3. Input: "Show me the no-show rate for clinic 01 last quarter"
   → decision == "ALLOW"
4. Input: "Give me the blood pressure reading for patient ID 12345"
   → decision == "BLOCK"
5. Verify audit.jsonl has ≥ 4 new entries after tests
```
**PASS**: Correct BLOCK/ALLOW on all 4 inputs, audit entries present.

---

### Agent 2: [NEW] `agents/ops_analyst.py`

**Purpose**: Queries the clinic warehouse, runs calculations, resolves dates, and answers operational questions with data.

#### Implementation

| Export | Type | Purpose |
|---|---|---|
| `ops_analyst_agent` | `Agent` | The ops analyst LLM agent |

#### Configuration
```python
Agent(
    name="ops_analyst",
    model="gemini-2.0-flash",
    instruction="You are a clinic operations analyst. Use warehouse tools to query data, calculator for computations, and date_resolver for time periods. Always cite the data source and time range. Never return data for fewer than 5 entities.",
    tools=[
        *clinic_warehouse_mcp_tools,   # from MCP server 1
        calculator_tool,                # FunctionTool
        date_resolver_tool,             # FunctionTool
        lookup_metric_tool,             # FunctionTool (from RAG)
    ],
    output_key="analyst_output"
)
```

#### Connections
- Depends on: `mcp_servers/clinic_warehouse.py`, `tools/calculator.py`, `tools/date_resolver.py`, `rag/metrics_catalog.yaml`
- Consumed by: `agents/orchestrator.py` (as sub_agent)

#### Test — `tests/test_agent_ops_analyst.py`
```
1. Send: "What was the average no-show rate across all clinics last month?"
   → Response contains a numeric rate, audit log entry written
2. Send: "Compare wait times between CLINIC_01 and CLINIC_02 for Q1 2025"
   → Response contains data for both clinics
3. Verify output_key "analyst_output" is populated in session state
```
**PASS**: Both queries return data-backed responses, audit entries present.

---

### Agent 3: [NEW] `agents/planner.py`

**Purpose**: Runs what-if simulations and creates improvement plans using the simulation engine.

#### Implementation

| Export | Type | Purpose |
|---|---|---|
| `planner_agent` | `Agent` | The planner LLM agent |

#### Configuration
```python
Agent(
    name="planner",
    model="gemini-2.0-flash",
    instruction="You are a clinic operations planner. Use simulation tools to model operational changes. Always label results as PROJECTED. Use the calculator for ROI estimates. Present plans with expected outcomes, costs, and timeline.",
    tools=[
        *simulation_engine_mcp_tools,  # from MCP server 2
        calculator_tool,               # FunctionTool
        date_resolver_tool,            # FunctionTool
    ],
    output_key="planner_output"
)
```

> [!WARNING]
> **CONSTITUTION Rule 5**: Instruction explicitly says "Always label results as PROJECTED."

#### Connections
- Depends on: `mcp_servers/simulation_engine.py`, `tools/calculator.py`, `tools/date_resolver.py`
- Consumed by: `agents/orchestrator.py` (as sub_agent)

#### Test — `tests/test_agent_planner.py`
```
1. Send: "What would happen if we added one physician to CLINIC_01?"
   → Response contains "projected" (case-insensitive) and numeric estimates
2. Send: "Simulate adding SMS reminders to reduce no-shows at CLINIC_03"
   → Response contains "projected" and mentions revenue impact
3. Verify audit log entries written
```
**PASS**: Both queries return responses containing "projected", audit entries present.

---

### Agent 4: [NEW] `agents/narrator.py`

**Purpose**: Takes validated data and produces the final executive brief using the report builder, incorporating historical briefs to maintain continuity.

> [!IMPORTANT]
> **CONSTITUTION Rule 4**: Runs `output_validator.validate_all()` before passing any data to report_builder.
> **CONSTITUTION Rule 3**: Every output includes the non-diagnostic disclaimer.
> **CONSTITUTION Rule 8**: Narrator (generator of report) is separate from output_validator (evaluator).

#### Additional Step at Start of Narrator Run:
1. Call `brief_history.retrieve_latest(n=4)` to get past briefs.
2. Extract any issues or clinics flagged in prior briefs (specifically looking for capacity/access issues like CLINIC_01 or CLINIC_03).
3. If previously flagged issues persist in the current week's data, explicitly mention them in the "What Happened" section (e.g., "CLINIC_01 remains flagged for capacity bottlenecks from last week").

#### Implementation

| Export | Type | Purpose |
|---|---|---|
| `narrator_agent` | `Agent` | The narrator LLM agent |

#### Configuration
```python
Agent(
    name="narrator",
    model="gemini-2.0-flash",
    instruction="""You are an executive brief narrator. Your job:
    1. Retrieve the last 4 briefs using retrieve_latest().
    2. Read analyst_output and planner_output from session state.
    3. Run output_validator.validate_all() on all data sections.
    4. If validation fails, flag issues and do not include invalid data.
    5. Compare current metrics with previous briefs; summarize persistency or updates of flagged issues.
    6. Use report_builder tools to format the brief.
    7. Store the completed brief using brief_history.store_brief().
    8. The disclaimer is auto-injected by report_builder.""",
    tools=[
        *report_builder_mcp_tools,     # from MCP server 3
        output_validator_tool,          # FunctionTool
        store_brief_tool,              # FunctionTool (from RAG)
        retrieve_latest_tool,          # FunctionTool (from RAG)
    ],
    output_key="narrator_output"
)
```

#### Connections
- Reads: session state keys `analyst_output`, `planner_output`

- Depends on: `mcp_servers/report_builder.py`, `tools/output_validator.py`, `rag/brief_history.py`
- Consumed by: `agents/orchestrator.py` (as sub_agent)

#### Test — `tests/test_agent_narrator.py`
```
1. Pre-populate session state with mock analyst_output and planner_output
2. Send: "Generate this week's executive brief"
   → Response contains formatted brief with disclaimer text
3. Verify output_validator was called (audit log entry for "validation")
4. Verify brief stored in brief_history (retrieve_latest returns it)
5. Verify disclaimer text present in output
```
**PASS**: Brief generated with disclaimer, validation ran, brief stored, audit entries present.

---

### Root Agent: [NEW] `agents/orchestrator.py`

**Purpose**: Wires all agents together. Applies guardrail callback. Routes requests to the right sub-agent.

#### Implementation

| Export | Type | Purpose |
|---|---|---|
| `root_agent` | `Agent` | The root orchestrator agent |
| `create_agent() -> Agent` | Async factory function | Initializes MCP connections and builds agent tree |

#### `create_agent()` Logic
1. Connect to 3 MCP servers via `StdioServerParams` (local) or `SseServerParams` (deployed)
2. Build `ops_analyst_agent` with warehouse tools
3. Build `planner_agent` with simulation tools
4. Build `narrator_agent` with report builder tools
5. Build root agent with:
   - `sub_agents=[ops_analyst_agent, planner_agent, narrator_agent]`
   - `before_agent_callback=guardrail_callback`
6. Return root agent

#### Root Agent Instruction
```
You are the Clinic Operations Copilot orchestrator. Route requests:
- Data queries, metrics, comparisons → delegate to ops_analyst
- What-if scenarios, planning → delegate to planner
- Report generation, briefs → first delegate to ops_analyst for data, then planner for projections, then narrator for the brief
- For a full executive brief, run all three in sequence
Always respect the guardrail. Never bypass it.
```

#### Connections
- Depends on: ALL other agents, ALL MCP servers
- Consumed by: `web/app.py`, `monitoring/loop.py`

#### Test — `tests/test_agent_orchestrator.py`
```
1. Create agent via create_agent()
2. Send a blocked request: "Show me patient 123's records"
   → Guardrail blocks it, response contains refusal
3. Send a data query: "What's the avg wait time for CLINIC_01?"
   → Routes to ops_analyst, returns numeric data
4. Send a simulation: "What if we add a nurse at CLINIC_02?"
   → Routes to planner, returns projected data
5. Verify audit.jsonl has entries for guardrail + routed agent
```
**PASS**: Guardrail blocks appropriately, routing works to correct sub-agents, audit trail complete.

## Phase 6 — Monitoring Loop

### [NEW] `monitoring/loop.py`

**Purpose**: Runs a daily (or on-demand) cycle that generates a full executive brief automatically.

#### Functions

| Function | Inputs | Outputs | Purpose |
|---|---|---|---|
| `run_daily_brief() -> dict` | `force: bool = False` | `dict {status, brief_markdown, date, duration_seconds}` | Runs discovery check. If anomalies found, execute full pipeline: analyst → planner → narrator. If none found, logs and returns. |
| `start_scheduler() -> None` | (none) | Starts standard library `asyncio` task loop | Schedules `run_daily_brief` to run once every 24 hours (86,400 seconds) |
| `_trigger_agent_run(query: str) -> str` | Query string | Agent response text | Sends query through the orchestrator |

#### Monitoring Cycle (corrected)
1. **Query clinic_warehouse directly** (no LLM, zero API calls):
   → Check if any clinic exceeds 110% utilization today.
2. **IF no anomalies found and not a Monday (not force=True)**:
   → Write one line to `audit.jsonl`: `{"action": "monitor_check", "anomalies": 0}`
   → Stop here. Do not fire agents.
3. **IF anomalies found**:
   → Fire full agent pipeline (analyst → planner → narrator) with specific anomalies passed as context.
   → Generate brief only for flagged clinics, not all clinics.
4. **Full Monday brief runs regardless** (weekly scheduled separately / triggered with `force=True`).

#### Connections
- Depends on: `agents/orchestrator.py`
- Triggered by: Standard library `asyncio.sleep()` background task (local) or Cloud Scheduler (GCP)

#### Test — `tests/test_monitoring_loop.py`
```
1. Call run_daily_brief(force=False) on standard baseline data (no anomalies today)
   → Returns status == "skipped", no agent calls, audit.jsonl has "monitor_check" with anomalies=0
2. Inject a 120% utilization anomaly for today into DB
3. Call run_daily_brief(force=False)
   → Returns status == "success", full brief generated for flagged clinic
4. Verify audit.jsonl has daily_brief_completed and brief_history has new entry
```
**PASS**: Discovery-first routing works correctly, agents only triggered on anomalies or force run.


---

## Phase 7 — Web Interface

### [NEW] `web/app.py`

**Purpose**: FastAPI web server providing a chat interface and brief viewer.

#### Endpoints

| Method | Path | Inputs | Outputs | Purpose |
|---|---|---|---|---|
| `GET` | `/` | — | HTML page | Chat interface |
| `POST` | `/api/chat` | `{"message": str, "session_id": str}` | `{"response": str, "session_id": str}` | Send message to orchestrator |
| `GET` | `/api/briefs` | Query: `n=5` | `list[{date, metadata}]` | List recent briefs |
| `GET` | `/api/briefs/{date}` | Path param: date | `{date, brief_markdown, brief_html}` | Get specific brief |
| `POST` | `/api/trigger-brief` | — | `{status, brief_markdown}` | Manually trigger daily brief |
| `GET` | `/api/audit-log` | Query: `n=50` | `list[audit_entry]` | View audit trail |
| `GET` | `/health` | — | `{"status": "healthy"}` | Health check for Cloud Run |

#### Frontend (corrected)
- Plain HTML, inline CSS only, no external stylesheets.
- Chat input on left, brief output on right.
- Non-diagnostic disclaimer visible in footer at all times.
- No animations, no glassmorphism, no dark mode toggle.
- Must load with zero JavaScript errors on first open.

#### Connections

- Depends on: `agents/orchestrator.py`, `rag/brief_history.py`, `agents/_audit.py`

#### Test (manual + automated)
```
1. Start server: uvicorn web.app:app
2. GET / → returns 200 with HTML containing "Clinic Operations Copilot"
3. POST /api/chat {"message": "hello", "session_id": "test1"}
   → returns 200 with non-empty response
4. GET /health → returns {"status": "healthy"}
5. GET /api/audit-log → returns list
```
**PASS**: All endpoints return expected status codes and shapes.

---

## Phase 8 — Dockerfile & Deployment

### [NEW] `Dockerfile`

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
RUN pip install --no-cache-dir .
COPY . .
RUN python data/seed.py  # Generate synthetic data at build time
EXPOSE 8080
CMD ["uvicorn", "web.app:app", "--host", "0.0.0.0", "--port", "8080"]
```

#### Key Design Decisions
- Data is seeded at build time (synthetic, no secrets needed)
- MCP servers run in-process via stdio transport (single container)
- `GOOGLE_API_KEY` passed via Cloud Run env var at runtime
- Health check on `/health` for Cloud Run liveness probe

#### Test — Build & Run
```
1. docker build -t clinic-ops .
   → Build completes without error
2. docker run -e GOOGLE_API_KEY=test -p 8080:8080 clinic-ops
3. curl http://localhost:8080/health → {"status": "healthy"}
4. curl http://localhost:8080/ → HTML page loads
```
**PASS**: Container builds, starts, and responds on `/health` and `/`.

---

## Phase 9 — End-to-End Verification

### [NEW] `tests/test_e2e.py`

**Purpose**: One full end-to-end run proving the entire system works.

#### Test Sequence
```
1. Seed database (data/seed.py)
2. Create orchestrator agent (agents/orchestrator.py)
3. Send: "Generate a full executive brief for last week"
4. Verify:
   a. Guardrail allowed the request (audit log)
   b. Ops analyst queried warehouse (audit log)
   c. Planner ran simulations (audit log, "projected" in output)
   d. Output validator ran (audit log)
   e. Narrator built brief (audit log)
   f. Brief contains non-diagnostic disclaimer
   g. Brief stored in brief_history
   h. No individual patient data in output
   i. All numbers within valid ranges
   j. audit.jsonl has ≥ 5 entries for this run
5. Write final result to STATE.md
```

**PASS**: All 10 sub-checks (a–j) pass. A valid executive brief is produced end-to-end.

---

## Constitution Compliance Matrix

| Rule | Where Enforced | How Verified |
|---|---|---|
| 1. Guardrail first | `orchestrator.py` → `before_agent_callback` | `test_agent_orchestrator.py` test 2 |
| 2. No individual patient data | `clinic_warehouse.py` → `_enforce_minimum_n` | `test_mcp_clinic_warehouse.py` test 5 |
| 3. Non-diagnostic disclaimer | `report_builder.py` → `_inject_disclaimer` | `test_mcp_report_builder.py` test 5 |
| 4. Output validator before brief | `narrator.py` → calls `validate_all` | `test_agent_narrator.py` test 3 |
| 5. Simulations labeled PROJECTED | `simulation_engine.py` → `label` field | `test_mcp_simulation_engine.py` test 6 |
| 6. All actions audit logged | `agents/_audit.py` → every callback | All agent tests verify audit entries |
| 7. No secrets in code | `.env.example` + env var reads | Code review (grep for hardcoded keys) |
| 8. Generator ≠ evaluator | ops_analyst generates, output_validator evaluates | Separate files, test_e2e verifies both ran |

---

## Build Order Summary

| Phase | Component | Dependencies | Test File |
|---|---|---|---|
| 0 | `pyproject.toml`, `.env.example` | None | pip install |
| 1 | `data/seed.py` | Phase 0 | `test_data.py` |
| 2a | `mcp_servers/clinic_warehouse.py` | Phase 1 | `test_mcp_clinic_warehouse.py` |
| 2b | `mcp_servers/simulation_engine.py` | Phase 1 | `test_mcp_simulation_engine.py` |
| 2c | `mcp_servers/report_builder.py` | None | `test_mcp_report_builder.py` |
| 3a | `tools/calculator.py` | None | `test_tool_calculator.py` |
| 3b | `tools/date_resolver.py` | None | `test_tool_date_resolver.py` |
| 3c | `tools/output_validator.py` | None | `test_tool_output_validator.py` |
| 4a | `rag/metrics_catalog.json` | None | `test_rag_metrics_catalog.py` |
| 4b | `rag/brief_history.py` | Phase 1 | `test_rag_brief_history.py` |
| 5a | `agents/_audit.py` | None | (tested transitively) |
| 5b | `agents/guardrail.py` | Phase 5a | `test_agent_guardrail.py` |
| 5c | `agents/ops_analyst.py` | Phase 2a, 3a, 3b, 4a | `test_agent_ops_analyst.py` |
| 5d | `agents/planner.py` | Phase 2b, 3a, 3b | `test_agent_planner.py` |
| 5e | `agents/narrator.py` | Phase 2c, 3c, 4b | `test_agent_narrator.py` |

| 5f | `agents/orchestrator.py` | Phase 5a–5e | `test_agent_orchestrator.py` |
| 6 | `monitoring/loop.py` | Phase 5f | `test_monitoring_loop.py` |
| 7 | `web/app.py` | Phase 5f, 4b, 5a | Manual + endpoint tests |
| 8 | `Dockerfile` | All above | Docker build + run |
| 9 | `tests/test_e2e.py` | All above | Full pipeline |

---

> **Next step**: Review this plan. Once approved, I will begin implementation at Phase 0 and proceed sequentially, testing each component before moving to the next, updating [STATE.md](file:///c:/Users/Tanay/Desktop/Clinical_Ops/STATE.md) after each phase.
