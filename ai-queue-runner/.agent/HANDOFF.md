FROM: codex
TO: claude
TASK: T043
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- README.md, config.example.toml, docs/AI_GUIDE.md, docs/ARCHITECTURE.md
- src/ai_queue_runner/domain/enums.py, config/defaults.py
- src/ai_queue_runner/infrastructure/adapters/{__init__,gemini}.py
- src/ai_queue_runner/prompts/gemini_wrapper.txt
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/application/{cli_service,daily_summary_formatter,execution_service,scheduler_service,task_service,worker,workload_planning}.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/{main_window,task_dialog,task_table,tray}.py
- tests/integration/{test_execution_service,test_repositories,test_runtime,test_task_reservation}.py
- tests/unit/{test_adapters,test_config_loader,test_main,test_ui,test_workload_planning}.py
- .agent/STATE.md, .agent/TASKS.md, .agent/DECISIONS.md, .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 368 passed in 24.34s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean (line-ending warnings only)

NOTES:
- Added Gemini to AgentName/AgentPolicy, config defaults, CLI choices, worker/GUI composition,
  task controls, status panels, tray controls, daily summaries, and persisted enum paths.
- Gemini defaults to enabled=false. Runtime reservation requires enabled config and
  `is_installed()`, so an absent CLI cannot change existing Claude/Codex scheduling.
- GeminiAdapter uses the documented headless JSON contract, stdin prompt wrapper,
  sandboxed yolo approval, config-driven limit patterns, fake JSON classification,
  response summarization, and relative quota-reset parsing. Exit code 0 remains authoritative.
- Generalized two-agent assumptions: adapter/runtime registration, AgentName iteration,
  Any fallback selection, plan-batch budgets/accounting/output, CLI agent choices,
  GUI/task/tray labels, daily run/limit counts, docs, and legacy DB constraints.
- Any fallback keeps the existing preferred target for the normal two-agent case. With three
  agents, if that target is unavailable, the scheduler excludes the last failed agent and
  allows another available eligible agent; it retries the failed agent only to avoid starvation.
- TokenBudgets accepts optional `gemini` (default 0). Legacy two-key JSON and its two-agent
  output maps remain unchanged; a positive Gemini budget adds Gemini to planning/output.
- Schema v12 expands legacy CHECK value lists to Gemini using a transactional, retry-safe
  table rebuild that preserves existing columns, rows, and explicit indexes.
- Removed the completed Gemini adapter item from plan section 20.

RISKS:
- Gemini CLI is not installed here, so no version floor or real invocation was verified.
- Official docs confirm `--prompt`, `--output-format json`, `--approval-mode`, `--sandbox`,
  JSON `response`/`stats`/optional `error`, and exit codes. It remains uncertain whether the
  eventual installed Windows version accepts stdin plus `--prompt ""`, whether sandboxed
  `approval-mode yolo` covers every required tool without prompts, and whether error JSON or
  quota-reset wording is stable.
- Relative reset parsing is based on documented/official-repository examples such as
  `quota will reset after 8h44m7s`; unseen absolute timestamps or new wording will fall back
  to default_cooldown_minutes until measured and added to the isolated parser/config patterns.
