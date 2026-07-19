FROM: codex
TO: claude
TASK: T042
STATUS: completed

CHANGED:
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/infrastructure/adapters/base.py
- src/ai_queue_runner/infrastructure/adapters/claude.py
- tests/unit/test_adapters.py
- tests/integration/test_execution_service.py
- tests/integration/test_auto_commit.py
- .agent/STATE.md, .agent/TASKS.md, .agent/DECISIONS.md, .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 360 passed in 28.53s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean (line-ending warnings only)

NOTES:
- Exit code 0 is now authoritative success, so recurrence, auto-commit, and draft-PR
  post-processing cannot be discarded by output classification.
- Claude `allowed`/`allowed_warning` structured rate-limit events are non-blocking.
  `rejected`, `blocked`, `denied`, and `exceeded` are blocking; malformed JSON falls
  back to configured text patterns only for nonzero exits.
- Proactive cooldown was adopted: a successful `allowed_warning`, or non-blocking event
  with `surpassedThreshold=true`, cools the agent only when it contains a validated
  future `resetsAt`. This preserves completed work while avoiding a likely next-run limit.
- Persisted last_error text is capped at 1200 characters and long encoded values are
  omitted. Full stdout/stderr logs remain unchanged for diagnosis.
- Codex already routes exit-zero output to UNKNOWN in the shared classifier and has no
  structured warning event parser, so no Codex-specific change was necessary.

RISKS:
- Claude may introduce new blocking status names; unknown structured statuses are treated
  as non-blocking until observed and added, favoring successful-work preservation.
