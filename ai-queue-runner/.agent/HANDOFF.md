FROM: codex
TO: claude
TASK: T032
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
- src/ai_queue_runner/infrastructure/repositories.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/calendar_view.py
- tests/integration/test_application_services.py
- tests/unit/test_main.py
- tests/unit/test_ui.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q tests\integration\test_application_services.py tests\unit\test_main.py tests\unit\test_ui.py` -> 80 passed in 6.13s
- `..\.venv\Scripts\python -m pytest -q` -> 288 passed in 24.06s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> no whitespace errors (Git emitted only existing LF/CRLF conversion warnings)
- `..\.venv\Scripts\python -m ai_queue_runner --help` -> `daily-summary` listed

NOTES:
- `TaskRepository.list_terminal_activity_between()` and `TaskRunRepository.list_finished_between()` own the range queries; `TaskService.daily_summary()` aggregates their rows into immutable project and total DTOs.
- A selected local date becomes a half-open `[local midnight, next local midnight)` interval, then both boundaries are converted to naive UTC. Completed runs and terminal tasks are attributed by their finish time; blocked tasks use `updated_at` because user-rejected reservations have no `finished_at`.
- Project summaries include succeeded/failed/blocked/cancelled counts, completed-run duration, per-agent run counts, fallback count, unique recorded worktree branches, and failed-task ID/title/latest error type. Totals add usage-limit occurrence flags per agent.
- `daily-summary [--date YYYY-MM-DD] [--json]` supports an empty successful result and defaults to the current local date. `docs/AI_GUIDE.md` documents the complete JSON contract.
- The calendar tab adds one `이 날짜 요약` button backed by the same application service. No tab or persistent UI state was added.
- DB schema remains v5; no columns or migrations were added. The implemented plan §20 bullet was removed.

RISKS:
- The existing schema records runs but not a full task-status event log. If a failed/blocked task is later retried or cancelled, its earlier status cannot be reconstructed; summaries intentionally report the task's current terminal outcome for the selected date.
- A run crossing local midnight contributes its full duration to the date on which it finishes, matching result-oriented morning reporting rather than wall-clock utilization per day.
- Worktree branch lists can only include branch values persisted on the task or completed task-run rows; branch creation attempts that never persisted either value are not recoverable.
