FROM: codex
TO: claude
TASK: T023 + T024
STATUS: completed

CHANGED (T023):
- src/ai_queue_runner/ui/calendar_view.py
- src/ai_queue_runner/ui/main_window.py
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/application/task_service.py
- tests/unit/test_ui.py
- tests/integration/test_application_services.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

CHANGED (T024):
- src/ai_queue_runner/main.py
- src/ai_queue_runner/application/cli_service.py
- tests/unit/test_main.py
- README.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- T023: `..\.venv\Scripts\python -m pytest -q` -> 239 passed in 11.97s
- T023: `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- T024/final: `..\.venv\Scripts\python -m pytest -q` -> 245 passed in 12.58s
- T024/final: `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!

NOTES:
- T023 adds a local-time calendar tab backed only by TaskService activity queries. It
  marks created/finished/scheduled dates, groups the selected day's tasks, focuses the
  main queue table on double-click, and presets new tasks to 09:00 local time while
  persisting the existing not_before field as naive UTC.
- T024 adds Qt-free add-task/list-tasks/show-task/cancel-task argparse subcommands using
  the existing config selection and TaskService validation. Text and stable JSON output
  are supported; JSON enum values are strings and datetimes are UTC ISO-8601 values.
- CLI validation/policy errors exit 2 with a one-line Korean message; unexpected errors
  exit 3 without a traceback. A fresh-process test confirms PySide6 is not imported.
- README includes copy-pasteable AI/CLI examples and documents local --not-before input.

RISKS:
- The first full-suite run reached the pre-existing startup-registration test before it
  was isolated and re-exported the effective config to
  `%LOCALAPPDATA%\AIQueueRunner\config.toml` at 16:09:33. The test now redirects that
  export to tmp_path; the final full-suite rerun left the real file timestamp unchanged.
  Both repository and LOCALAPPDATA queue.db timestamps remained earlier and unchanged.
