FROM: codex
TO: user
TASK: T041
STATUS: completed

CHANGED:
- src/ai_queue_runner/ui/main_window.py
- tests/unit/test_ui.py
- README.md
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- .agent/STATE.md, .agent/TASKS.md, .agent/DECISIONS.md, .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 347 passed in 30.93s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean (line-ending warnings only)

NOTES:
- Refresh is 1 second for a running GUI scheduler, 5 seconds for external-worker
  observation, and 10 seconds while idle or paused.
- The existing refresh button remains connected to an immediate refresh.
- Timer interval changes are covered in the UI regression tests.

RISKS:
- None identified.
