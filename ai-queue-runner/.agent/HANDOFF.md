FROM: claude
TO: (review)
TASK: T025 + T026
STATUS: done

CHANGED:
- src/ai_queue_runner/ui/main_window.py — approval dedup (request_approval registers
  reservation id; nested-event-loop guard `_approval_dialog_open`), run-now auto tasks
  execute without dialog, reserved-task cancel now rejects then cancels.
- src/ai_queue_runner/application/runtime.py — reject(block=True) parks task in BLOCKED
  with last_error so the scheduler cannot immediately re-reserve/re-prompt.
- src/ai_queue_runner/main.py, application/cli_service.py — new subcommands:
  retry-task, update-task (partial overlay), duplicate-task, move-top, archive-task,
  delete-task, set-agent, queue-status. All support --json.
- docs/AI_GUIDE.md (new), README.md — AI-facing CLI contract and automation setup.
- tests: unit/test_ui.py (4 new), integration/test_runtime.py (1 new), unit/test_main.py (4 new).

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 254 passed in 10.95s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!

NOTES:
- Rejecting an approval now leaves the task BLOCKED (was: WAITING → immediate
  re-reserve → re-prompt loop). retry-task / GUI 재시도 returns it to the queue.
- CLI approval default remains `confirm`; autonomous flows must pass --approval auto
  (documented in AI_GUIDE).
- queue-status cannot tell whether a GUI scheduler is actually running (cross-process).

RISKS:
- dist exe still predates T017+; needs scripts/build_windows.ps1 rebuild.
