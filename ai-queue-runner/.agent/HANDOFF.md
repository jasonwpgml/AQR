FROM: codex
TO: claude
TASK: T033
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
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/application/workload_planning.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/main.py
- tests/integration/test_application_services.py
- tests/integration/test_repositories.py
- tests/unit/test_main.py
- tests/unit/test_workload_planning.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 297 passed in 23.17s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- targeted service/CLI/migration/planning suite -> 68 passed in 6.41s
- `..\.venv\Scripts\python -m ai_queue_runner --help` -> `split-task` listed
- `git diff --check` -> no whitespace errors (Git emitted only existing LF/CRLF conversion warnings)

NOTES:
- `depends_on_indices` is validated as a zero-based sibling DAG before persistence. After every child receives an ID, sibling references and existing `dependency_ids` are stored together as ordinary `task_dependencies` rows in one transaction.
- Schema v6 adds indexed `tasks.split_parent_task_id`; `TaskView` and CLI records expose `split_parent_task_id` plus `split_child_ids`, and text `list-tasks` shows the same relation.
- `split-task ID plan.json` uses the existing token plan contract. It accepts only waiting/retry_wait/blocked originals, CAS-cancels the original, inserts children and relations, and rewires every direct dependent from the original to all children before one commit.
- Downstream tasks therefore require every child to succeed. A failed, cancelled, or incomplete child intentionally keeps downstream work ineligible until the queue is manually corrected.
- `--dry-run` performs token, path, existing dependency, sibling DAG, original status, and split-cycle checks without changing the database.
- The CAS cancellation is the transaction's first write. A later child insert or rewiring failure rolls back the original status, children, split relations, and dependency changes together.
- AQR does not call an LLM; Claude/Codex supplies the split plan JSON.

RISKS:
- Split parents cannot be deleted through `TaskService.delete()` while child relation rows exist, preserving relationship visibility. Delete children first if permanent history removal is intended.
- The dependency policy intentionally does not auto-cancel or auto-retry downstream work when a child fails; operators must resolve the child outcome explicitly.
- `.agent/TASK_T033.md` and `.agent/TASK_T034.md` are untracked user/task files and were left untouched.
