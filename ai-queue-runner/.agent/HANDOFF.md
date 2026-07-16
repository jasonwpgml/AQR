FROM: codex
TO: claude
TASK: T019
STATUS: completed

CHANGED:
- src/ai_queue_runner/infrastructure/adapters/base.py
- src/ai_queue_runner/infrastructure/adapters/claude.py
- src/ai_queue_runner/infrastructure/adapters/codex.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/config/defaults.py
- config.example.toml
- tests/unit/test_adapters.py
- tests/integration/test_execution_service.py
- tests/integration/test_runtime.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 209 passed in 10.86s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean
- build_command source audit -> unchanged for ClaudeAdapter and CodexAdapter
- Scope audit -> only T019-permitted implementation, test, and handoff files changed; `data/queue.db` untouched

NOTES:
- Claude reset extraction reads rejected stream-json rate_limit_event resetsAt values exactly as naive UTC; allowed and malformed events remain ignored.
- Codex reset extraction conservatively accepts explicit UTC/offset timestamps, Unix seconds, and `try again in` hour/minute durations. Past and more-than-seven-day reset times fall back instead of being trusted.
- Usage-limit handling scans the 64 KiB output tails and always records an automatic available_at, using the per-agent default_cooldown_minutes (default 300) when extraction fails. The selected UTC time is included in task.last_error.
- QueueRuntime already promoted due cooldowns before reservation, so runtime code was unchanged; regression coverage now proves both due promotion/reservation and future cooldown preservation.
- Existing AIDEV anchors were reviewed after editing; none became stale and no new non-obvious constraint required an anchor.

RISKS:
- None known.
