# Task backlog

Phases are defined in `../../AI_QUEUE_RUNNER_PLAN.md` §14.

[done] T001 Phase 0: pyproject, package skeleton, config loader, logging, pytest, ruff
[done] T002 Phase 1: enums, state machine, SQLAlchemy models
[done] T003 Phase 1: DB init, repositories, CRUD/persistence tests
[done] T004 Phase 2: candidate filtering
[done] T005 Phase 2: scoring and deterministic tie-breaking
[done] T006 Phase 2: atomic CAS reservation and concurrency test
[done] T007 Phase 2: reserved-timeout recovery
[done] T008 Phase 3: ProcessRunner, cancellation, timeout, log capture
[done] T009 Phase 3: FakeAdapter execution, result flows, retry, crash recovery
[done] T009b Phase 3: PID-based startup recovery
[done] T010 Phase 4: inspect installed Claude/Codex CLI contracts
[done] T011 Phase 4: Claude adapter and limit/auth classification
[done] T012 Phase 4: Codex adapter and sandboxed execution
[done] T013 Phase 5: main window, task table, task dialog
[done] T014 Phase 5: agent status, available-at controls, approval dialog
[done] T015 Phase 5: log viewer, tray controls, notifications
[done] T016 Phase 6: stabilization, backup/export/startup, package, soak, and completion audit
[done] T017 UX: human-readable log rendering + result summaries
[done] T018 UX: allowed-roots settings dialog with live apply
[done] T019 auto usage-limit reset detection (Claude exact, Codex best-effort)
[done] T020 prompt templates (settings-backed, task dialog integration)
[done] T021 exit warning, task deletion, CLI path resolution, retry_wait visibility
[done] T022 task dialog restricts project path to allowed roots
[done] T023 calendar view with scheduled-date task creation
[done] T024 headless CLI add/list/show/cancel subcommands
[done] T025 approval UX: dedup dialogs, reject→blocked, auto run-now, reserved cancel fix
[done] T026 headless CLI full queue control (update/retry/duplicate/move-top/archive/delete/set-agent/queue-status) + AI guide
[done] T027 token-aware workload planning, deterministic Claude/Codex distribution, and atomic batch enqueue
