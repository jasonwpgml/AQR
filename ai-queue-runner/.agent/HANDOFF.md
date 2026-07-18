FROM: codex
TO: claude
TASK: T038
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/TASK_T038.md
- .agent/HANDOFF.md
- README.md
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/domain/enums.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/application/task_template_service.py
- src/ai_queue_runner/application/recurrence_service.py
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/ui/task_table.py
- src/ai_queue_runner/ui/main_window.py
- tests/integration/test_repositories.py
- tests/integration/test_task_provenance.py
- tests/integration/test_execution_service.py
- tests/integration/test_task_templates.py
- tests/unit/test_main.py
- tests/unit/test_ui.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 325 passed in 45.80s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean (line-ending warnings only)

NOTES:
- Schema v10 adds task/template `created_by` and `creator_session_id`. Existing rows migrate to `unknown`; new GUI work defaults to `user`, and headless callers use global `--actor`/`--actor-session`.
- `task_events` stores management action, actor/session, task ID plus original creation time, project, title, and details. It intentionally has no task FK, so hard deletion preserves history.
- `list-tasks` filters by project, creator, session, and active state. Text output now includes creator and full project path; JSON includes stable provenance fields.
- `delete-tasks --project ...` permanently deletes eligible tasks inside an explicit project scope, resolving in-scope dependency order and returning skipped IDs/reasons. No human confirmation was added.
- `show-task` includes recent management events, while `list-events` can query current or deleted task history.
- Recurring successors preserve the original task provenance and record a system-created event. Templates, splits, rework, and duplication use the current command actor for newly created tasks.
- GUI adds a project filter, creator column, creator session, and recent management history in task details.
- `init_db()` now holds one SQLite `BEGIN IMMEDIATE` transaction across legacy version inference, `create_all`, and migrations. A 4-thread/8-initialization regression test covers the previously observed duplicate-column race.
- The pre-existing uncommitted T036/T037 changes were preserved. No Git commit was created.

RISKS:
- The currently running packaged executable is still the older build. Close, rebuild, and restart it before expecting the v10 fields/UI; first launch will migrate its local DB.
- `task_events` covers TaskService management mutations and recurring creation, not every scheduler reservation/execution status transition; those remain represented by current task state and `task_runs`.
- Project bulk deletion is intentionally partially successful: eligible tasks commit even when other matched tasks are skipped.
