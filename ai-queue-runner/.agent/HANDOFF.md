FROM: codex
TO: claude
TASK: T009
STATUS: completed

CHANGED:
- src/ai_queue_runner/infrastructure/adapters/__init__.py
- src/ai_queue_runner/infrastructure/adapters/base.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/application/recovery_service.py
- src/ai_queue_runner/config/defaults.py
- config.example.toml
- tests/fakes.py
- tests/integration/test_execution_service.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/DECISIONS.md
- .agent/HANDOFF.md

VERIFIED:
- ..\.venv\Scripts\python -m pytest -q
  -> 100 passed in 4.08s
- ..\.venv\Scripts\python -m ruff check src tests
  -> All checks passed!
- rg -n "shell=True|os.system" src/
  -> no matches
- rg -n "\.status =" src/ai_queue_runner/application/
  -> only SQL query comparisons and agent-state assignments; no task.status assignment
- git diff --check
  -> no whitespace errors

NOTES:
- ExecutionService commits reserved -> running, BUSY agent state, and the TaskRun before
  spawning, preserving D019's duplicate-execution safety boundary.
- Success, retry exhaustion, usage limit, timeout, spawn failure, and cancellation all persist
  their required task/run/agent outcomes; cancellation is committed before re-raising.
- Usage-limit matching is case-insensitive and bounded to log tails; subprocess environments are
  neither supplied nor persisted.
- promote_retry_tasks owns its transaction and returns due retry_wait tasks to waiting.
- FakeAdapter remains command-configurable and reusable for Phase 4/5 integration tests.

RISKS:
- T009b still needs PID-based startup recovery for tasks left in running state (plan §8.5).
