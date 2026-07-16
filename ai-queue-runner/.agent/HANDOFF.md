FROM: codex
TO: maintenance
TASK: T001-T016 / AI Queue Runner v0.1
STATUS: completed

DELIVERED:
- Persistent SQLite queue with strict state transitions, dependencies, scoring, project locks, and atomic per-agent reservation
- Async Claude/Codex process execution with stdin prompts, streamed logs, timeout/cancel, retry, usage-limit/auth handling, and PID+creation-time crash recovery
- Safe absolute-path/allowed-root policy and conservative CLI flags
- PySide6 desktop task management, immediate run, move-to-top, archive, approvals, logs, agent availability, tray controls, and lifecycle notifications
- SQLite online backup, effective-config export, per-user Windows startup registration, and PyInstaller onedir packaging
- UTF-8 README, architecture, design, third-party notices, build and soak scripts

VERIFIED 2026-07-16:
- `python -m pytest -q` -> 186 passed
- `python -m ruff check src tests` -> clean
- Source Qt offscreen smoke -> exit 0
- 20-task queue with mid-run runtime restart -> passed; accelerated soak wrapper -> passed
- Real Codex noninteractive smoke -> `QUEUE_SMOKE_OK`
- Real Claude Code 2.1.211 noninteractive/no-session smoke -> `QUEUE_CLAUDE_SMOKE_OK`
- Claude's successful `rate_limit_event(status=allowed)` captured and covered by classifier regression
- PyInstaller 6.21.0 onedir build -> `dist/AIQueueRunner/AIQueueRunner.exe`
- Packaged version, hidden GUI, config export, verified DB backup -> exit 0
- Packaged Qt `qwindows.dll` -> present
- Ralph-scoped anti-slop pass -> redundant service aliases removed, duplicated availability logic consolidated, fail-safe paths regression-tested

SAFETY INVARIANTS:
- Empty `allowed_project_roots` blocks execution.
- Prompts never enter shell strings or persisted command arrays.
- Recovery kills a PID only when exact OS creation time still matches; unknown or reused identities are not killed.
- Status changes go through `TaskRepository.set_status()`; direct task status assignment remains confined to that repository.
- Project exclusivity is rechecked inside the reservation CAS, so concurrent agents cannot reserve different tasks for the same project.
- Unknown config keys/agent tables are hard errors; partial agent configuration preserves both built-ins.

REMAINING VALIDATION GAP:
- The deterministic 20-task soak scenario passes, but the literal eight-hour wall-clock run was not held open in this session. Run `scripts/run_soak.ps1` for that operational-duration qualification.

NOTES:
- PyInstaller reports missing optional SQLAlchemy database drivers only; SQLite/PySide packaged smokes pass.
- Qt/PySide redistribution obligations are summarized in `THIRD_PARTY_NOTICES.md` and must be reviewed before external distribution.
