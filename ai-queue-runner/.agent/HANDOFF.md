FROM: codex
TO: claude
TASK: T018
STATUS: completed

CHANGED:
- src/ai_queue_runner/ui/settings_dialog.py
- src/ai_queue_runner/ui/main_window.py
- src/ai_queue_runner/main.py
- tests/unit/test_ui.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 203 passed in 8.75s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `..\.venv\Scripts\python -m pytest -q tests\unit\test_main.py tests\unit\test_ui.py` -> 24 passed in 1.36s
- Scope audit -> only T018-permitted implementation, test, and handoff files changed; `data/queue.db` untouched

NOTES:
- The Korean settings dialog validates absolute existing directories, compares resolved paths to prevent duplicates, warns when no roots remain, and exports the effective config to the active path.
- Save slice-mutates `config.app.allowed_project_roots` on the shared `AppConfig`, so the existing `TaskService` applies new roots immediately; export failure restores the prior in-memory list.
- `main.py` selects an explicit path first, then cwd `config.toml`, then the per-user app-data path. The no-file fallback remains optional at the `run_desktop_app` boundary to preserve its existing composition-test contract.
- Existing and new AIDEV anchors were reviewed; one note preserves the shared-config live-apply invariant.

RISKS:
- None known.
