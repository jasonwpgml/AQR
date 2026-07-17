FROM: codex
TO: claude
TASK: T030
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/application/recurrence_service.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/task_dialog.py
- tests/integration/test_execution_service.py
- tests/integration/test_repositories.py
- tests/unit/test_main.py
- tests/unit/test_ui.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 277 passed in 25.47s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> no whitespace errors (Git emitted only existing LF/CRLF conversion warnings)

NOTES:
- Schema v4 adds nullable positive `tasks.recurrence_interval_days` (1=daily, 7=weekly, N=N days) and nullable unique self-FK `recurrence_source_task_id`.
- `ExecutionService` invokes `RecurrenceService` only on a successful result. The succeeded status, completion timestamp, and waiting successor are committed in the same transaction.
- The successor copies title, prompt, project path, agent policy, priority, token estimate, approval/retry settings, tags, dependencies, and recurrence interval. Runtime state, assignment, fallback, and retry count start clean; `not_before = completed_at + interval`.
- Reprocessing checks the existing successor by source ID, while the database unique constraint is the final duplicate guard. A crash cannot commit success without its successor or vice versa.
- Failed, blocked, and cancelled paths do not invoke recurrence generation. Clearing the rule with `update-task --clear-repeat`, selecting no recurrence in the GUI, or cancelling the current occurrence stops the chain.
- CLI supports `--repeat daily|weekly|N`, `--clear-repeat`, JSON recurrence fields, a list REPEAT column, and show-task details. The GUI dialog supports none/daily/weekly/custom-day rules.
- The successor's `not_before` is consumed by the existing calendar activity query, so T023 required no calendar-specific code change.
- Removed only the implemented recurring-task bullet from plan section 20.

RISKS:
- Cadence is completion-based, so a long-running or delayed occurrence shifts later occurrences instead of backfilling missed wall-clock slots.
- Pre-existing untracked `.agent/TASK_T030.md` and an external modification to `../.claude/skills/codex-delegate/SKILL.md` were not modified.
