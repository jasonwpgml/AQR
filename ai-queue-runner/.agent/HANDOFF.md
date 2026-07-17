FROM: codex
TO: claude
TASK: T029
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md
- config.example.toml
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/application/runtime.py
- src/ai_queue_runner/application/scheduler_service.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/config/defaults.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/ui/main_window.py
- tests/integration/test_execution_service.py
- tests/integration/test_repositories.py
- tests/unit/test_config_loader.py
- tests/unit/test_main.py
- tests/unit/test_scheduler.py
- tests/unit/test_ui.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 270 passed in 20.78s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> no whitespace errors (Git emitted only existing LF/CRLF conversion warnings)

NOTES:
- Added schema v3: `tasks.fallback_agent` stores the current preferred target and `task_runs.fallback_to_agent` records each source run's transition target; v2 databases migrate additively.
- `ExecutionService` sets fallback only for Any tasks with retries remaining after the configurable consecutive-failure threshold (default 1); usage-limit failures set it immediately without incrementing `retry_count`.
- `SchedulerService` performs the preference filter before scoring. The previous agent is excluded only while the preferred agent is enabled, installed, currently available, and idle; the existing score/tie-break and CAS project/task claim remain unchanged.
- Dedicated policies and `fallback_enabled=false` bypass fallback. Successful runs and manual retry clear the current preference.
- `show-task` JSON/text and the desktop detail pane expose source -> target fallback history.
- Removed the implemented fallback item from plan section 20 and documented the rule in AI_GUIDE/ARCHITECTURE.

RISKS:
- Fallback execution begins on the next scheduler tick rather than interrupting the poll interval.
- When the preferred target is unavailable, the previous agent is deliberately eligible again to prevent permanent queue pinning.
- Pre-existing untracked `.agent/TASK_T029.md` and `../.claude/` were not modified.
