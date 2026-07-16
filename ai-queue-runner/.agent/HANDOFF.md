FROM: claude
TO: user / next agent
TASK: T006+T007 (Phase 2 atomic reservation + reserved-timeout recovery)
STATUS: completed

CHANGED:
- src/ai_queue_runner/application/scheduler_service.py (added reserve_next_task)
- src/ai_queue_runner/application/recovery_service.py (new)
- src/ai_queue_runner/infrastructure/database.py (SQLite busy timeout 30s)
- tests/integration/test_task_reservation.py (new, incl. 10-round race test)
- tests/integration/test_recovery.py (new)
- docs/ARCHITECTURE.md, README.md, .agent/* updates

VERIFIED:
- ../.venv/Scripts/python -m pytest -q            -> 82 passed
- ../.venv/Scripts/python -m ruff check src tests -> clean
- concurrency test re-run in isolation            -> 10 passed

NOTES:
- reserve_next_task owns its transaction and commit (selection + claim in one txn, D018).
- Both CAS updates must succeed (task still waiting AND agent has no current_task_id);
  otherwise rollback + re-select, up to max_attempts=3.
- release_stale_reservations only touches `reserved` tasks; `running` recovery is
  Phase 3's PID-based crash recovery (see AIDEV-NOTE in recovery_service.py).

RISKS:
- Phase 3 execution MUST transition reserved->running before spawning the CLI,
  or the timeout release could double-run a started task (D019 note).
- `now` parameter of reserve_next_task is reused across retry attempts only when
  caller passes it explicitly; tests do, production should pass None.
