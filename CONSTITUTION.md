# Non-negotiable rules for every file built in this project

1. Guardrail agent runs first on every request. No exceptions.
2. No individual patient data ever returned. Aggregates of 5+ only.
3. Non-diagnostic disclaimer on every output.
4. Output validator runs before any number enters a brief.
5. Simulation results always labeled projected, never actual.
6. Every agent action writes to /logs/audit.jsonl.
7. No secrets in code. Environment variables only.
8. Generator and evaluator are always separate agents. 
   Never the same agent checking its own work.