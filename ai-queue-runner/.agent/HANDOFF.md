FROM: codex
TO: claude
TASK: T034
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
- src/ai_queue_runner/domain/enums.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/main_window.py
- tests/integration/test_application_services.py
- tests/integration/test_repositories.py
- tests/unit/test_main.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 303 passed in 31.21s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- targeted service/CLI/migration suite -> 68 passed in 9.23s
- `..\.venv\Scripts\python -m ai_queue_runner --help` -> `review-task` and `pending-reviews` listed
- `git diff --check` -> no whitespace errors (Git emitted only LF/CRLF conversion warnings)

NOTES:
- Schema v7 adds append-only `task_reviews` (`task_id`, verdict, optional score/notes, reviewed_at) plus indexed `tasks.rework_of_task_id`. The highest review ID is the current evaluation; re-review appends a new row, and revision/review_count exposes prior evaluation history.
- `pending-reviews` selects succeeded tasks with no review rows, ordered by completion time and ID, with optional canonical project-path filtering.
- `review-task --verdict fail --reopen PROMPT` inserts the review and a linked waiting task in one caller-owned transaction. The rework task inherits project, agent policy, priority, estimated tokens, approval/retry settings, and user tags; it uses the supplied prompt and does not inherit recurrence.
- `list-tasks`, `show-task`, and the desktop task detail expose unreviewed/pass/fail plus score. No evaluation input UI was added.
- Daily-summary review counts were intentionally omitted: reviews commonly happen in a later batch, so mixing review time with the existing completion-date activity contract would be ambiguous and unnecessary for the pending-review workflow.
- The completed `result quality evaluation` item was removed from plan section 20.

RISKS:
- Re-review history has no dedicated CLI history command; current status and revision count are exposed, while full rows remain queryable in `task_reviews`.
- `.agent/TASK_T034.md` and `.agent/TASK_T035.md` are untracked user/task files and were left untouched.
