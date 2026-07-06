# Clinic Ops Copilot — Build Skill

## What you are building
A multi-agent healthcare operations system. 4 agents, 3 MCP servers, 
3 tools, 2 RAG retrievers, 1 monitoring loop.

## Build order (do not change this sequence)
1. Synthetic data (DuckDB)
2. MCP servers (clinic_warehouse first, then simulation_engine, 
   then report_builder)
3. Tools (calculator, date_resolver, output_validator)
4. RAG (metrics_catalog.yaml, brief_history retriever)
5. Agents (guardrail first, then ops_analyst, then planner, 
   then narrator, then main orchestrator)
6. Monitoring loop
7. Web interface
8. Dockerfile and Cloud Run deployment

## After every component built
- Run the test for that component
- Write result (PASS/FAIL + notes) to STATE.md
- Only move to next component if PASS
- If FAIL, fix the current component before moving on

## Verification rules
- MCP server verified by calling each tool and confirming 
  correct output
- Agent verified by running a test question through it
- Loop verified by triggering one full run and confirming 
  audit log entry written
- Do not self-verify. Use the verifier subagent.

## What not to do
- Do not add complexity not in GOAL.md
- Do not ask the user what to do next. Check STATE.md and proceed.
- Do not skip verification steps.

## Code style
Apply Ponytail rules to every file built. Run the YAGNI ladder 
before writing any function. Write the minimum that works. 
Do not install a library if stdlib does it. Do not write a class 
if a function does it. Do not write a function if one line does it.