# Task backlog

Phases are defined in ../AI_QUEUE_RUNNER_PLAN.md §14.

[done] T001 Phase 0: pyproject, package skeleton, config loader, logging, pytest, ruff
[done] T002 Phase 1: enums + state machine (transitions.py) + SQLAlchemy models
[done] T003 Phase 1: DB init (FK/WAL pragmas, schema version) + repositories + CRUD/persistence tests
[done] T004 Phase 2: candidate filter (status, not_before, policy, dependencies, project lock)
[done] T005 Phase 2: scoring + tie-breaking (plan §5.2–5.3, config-driven weights)
[done] T006 Phase 2: atomic reservation via CAS updates + 10-round concurrency test (plan §5.4)
[done] T007 Phase 2: reserved-timeout recovery (recovery_service.release_stale_reservations)
[done] T008 Phase 3: ProcessRunner (asyncio subprocess, cancel, timeout, log capture)
[done] T009 Phase 3: FakeAdapter + full success/fail/retry flow + crash recovery
[todo] T009b Phase 3: PID-based crash recovery for `running` tasks found at startup (plan §8.5)
[todo] T010 Phase 4: inspect installed claude/codex CLI --help; finalize command formats
[todo] T011 Phase 4: Claude adapter (run, stream output, classify limit/auth errors)
[todo] T012 Phase 4: Codex adapter (same responsibilities)
[todo] T013 Phase 5: main window + task table + task dialog
[todo] T014 Phase 5: agent status panel + available_at input + approval dialog
[todo] T015 Phase 5: log viewer + system tray + notifications
[todo] T016 Phase 6: stabilization, DB backup, PyInstaller build, long-run test
