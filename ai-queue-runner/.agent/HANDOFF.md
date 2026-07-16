FROM: codex
TO: claude
TASK: T022
STATUS: completed

CHANGED:
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/ui/main_window.py
- tests/unit/test_ui.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 235 passed in 14.12s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `..\.venv\Scripts\python -m pytest -q tests\unit\test_ui.py` -> 32 passed in 1.65s

NOTES:
- TaskDialog keeps the shared AppConfig reference and refreshes its non-editable root combo on every show, so settings changes are visible without restarting the app.
- The effective project path is read-only, defaults to the selected root, and accepts browsed folders only after strict resolved-path containment checks against the live allowed roots.
- Edit paths outside current roots remain visible with a warning but cannot be submitted until a root or valid subfolder is selected.
- TaskDraft still receives the selected project path as a plain string; TaskService path-policy validation remains unchanged.

RISKS:
- None known.
