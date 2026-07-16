FROM: codex
TO: claude
TASK: T021
STATUS: completed

CHANGED:
- src/ai_queue_runner/infrastructure/adapters/base.py
- src/ai_queue_runner/infrastructure/adapters/claude.py
- src/ai_queue_runner/infrastructure/adapters/codex.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/ui/main_window.py
- src/ai_queue_runner/ui/task_table.py
- src/ai_queue_runner/ui/agent_status_widget.py
- tests/unit/test_adapters.py
- tests/unit/test_ui.py
- tests/integration/test_application_services.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 227 passed in 15.14s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `..\.venv\Scripts\python -m pytest -q tests\unit\test_adapters.py tests\unit\test_ui.py tests\integration\test_application_services.py` -> 70 passed in 2.11s
- `git diff --check` -> clean (line-ending conversion warnings only)
- Scope audit -> only T021-permitted files changed; `data/queue.db` and LOCALAPPDATA AIQueueRunner data untouched
- AIDEV anchor audit -> scoped files searched before and after editing; existing anchors remain valid and one config-default discovery constraint was added

NOTES:
- Adapter construction now expands explicit paths, resolves PATH hits to absolute paths, probes the requested Windows install locations, and caches installation status; CLI flags are unchanged.
- Missing enabled CLIs show a Korean warning in the agent panel and disable availability controls; spawn failures include the attempted executable and a configuration remedy.
- Main-window and tray exit share one warning/cancellation path, while idle exit remains immediate.
- Task deletion is available by button and table context menu, blocks reserved/running tasks and prerequisites with dependents, and removes run history before deleting the task.
- retry_wait rows display the local not_before time and expose last_error through a tooltip and the existing detail pane.

RISKS:
- None known.
