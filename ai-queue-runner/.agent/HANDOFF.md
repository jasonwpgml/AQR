FROM: codex
TO: user/review
TASK: T027 + Markdown cleanup
STATUS: completed

CHANGED:
- application/workload_planning.py, task_service.py — explicit token budgets,
  deterministic Any-task distribution, and single-transaction batch creation.
- domain/models.py, infrastructure/database.py — persisted estimated_tokens and
  retry-safe schema v1 -> v2 migration.
- main.py, application/cli_service.py — plan-batch --dry-run/--json plus
  estimated-token add/update/output support.
- ui/task_dialog.py, ui/main_window.py — estimated-token editing and details.
- tests — planner, capacity failure, rollback, migration, CLI, and UI regressions.
- README/docs/.agent — JSON contract, architecture, decisions, and completion state.
- Removed 11 obsolete per-task Markdown instructions and refreshed the root README.

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 261 passed in 19.10s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `.\scripts\build_windows.ps1 -SkipInstall` -> Windows onedir build succeeded
- `..\.venv\Scripts\python -m ai_queue_runner --help` -> plan-batch listed

NOTES:
- Ordinary tasks may keep estimated_tokens=0 (unknown); plan-batch requires every
  estimate to be positive and persists Any tasks as the selected dedicated policy.
- Dedicated work is charged first. Any work is largest-first and chooses the lowest
  projected budget utilization; stable ties select Claude then input order.
- The first build attempt found a stale AQR process created by an earlier package
  smoke. That exact PID was stopped; the clean rebuild then completed successfully.

RISKS:
- Batch dependency_ids can reference existing DB tasks only, not siblings in the same batch.
- The accelerated soak coverage is green; the optional real-time 8-hour soak was not rerun.
