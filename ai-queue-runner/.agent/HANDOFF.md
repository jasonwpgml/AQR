FROM: codex
TO: claude
TASK: T035
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md
- README.md
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/application/task_template_service.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/main.py
- tests/integration/test_repositories.py
- tests/integration/test_task_templates.py
- tests/unit/test_main.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 308 passed in 49.03s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- targeted template/CLI/migration suite -> 48 passed in 10.17s
- `..\.venv\Scripts\python -m ai_queue_runner --help` -> all five template commands listed
- `git diff --check` -> no whitespace errors (Git emitted only LF/CRLF conversion warnings)

NOTES:
- Schema v8 adds `task_templates`, keyed by case-sensitive name, with title, prompt, nullable project path, agent/priority/token/approval/retry/recurrence/scheduling defaults, tags/dependency JSON, and created/updated timestamps. The v7->v8 registry migration uses `CREATE TABLE IF NOT EXISTS` and is covered by a double-invocation retry test.
- `save-template` upserts and reports `overwritten`; list/show/delete expose DB-backed CRUD. A project path can be deferred at save time but render/dry-run must produce a valid TaskDraft under the normal path/dependency policy.
- Only title/prompt support simple `{identifier}` placeholders. Missing names are aggregated and sorted; formatting/conversion expressions are rejected; substituted or overridden results containing unresolved simple placeholders are rejected. Explicit TaskDraft-field flags override stored values, and overriding title/prompt removes that field's variable requirements.
- `add-from-template --dry-run` renders and calls `TaskService.validate_batch()` without inserting a task. Actual creation still goes through `TaskService.create()`.
- Batch-only `depends_on_indices` is intentionally excluded from the single-task template schema; existing-task `dependency_ids` is persisted and validated.
- The completed work-template item was removed from plan section 20. No UI changes were made.

RISKS:
- Template names are intentionally case-sensitive because the SQLite TEXT primary key uses its default collation.
- Stored recurrence/not-before/tags/dependencies can be overridden but not explicitly cleared through `add-from-template`; delete/re-save the template or use `update-task` after creation when a clear operation is needed.
- Untracked `.agent/TASK_T035.md` and `.agent/TASK_T036.md` were user/task inputs and were left untouched.
