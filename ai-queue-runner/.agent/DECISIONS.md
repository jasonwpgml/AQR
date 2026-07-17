# Decisions

Settled decisions. Do not reopen without user approval.

D001 PySide6 for the desktop UI (plan §3.2)
D002 SQLite + SQLAlchemy 2.x for persistence (plan §3.1)
D003 Project-level exclusive lock only in v1 (plan §5.6)
D004 Agent availability times are entered manually in v1 (plan §7.1)
D005 Project lives in ai-queue-runner/ subfolder of the AQR repo (user, 2026-07-16)
D006 Agent-facing docs (AGENTS.md, .agent/) are written in English (user, 2026-07-16)
D007 Tooling: hatchling build backend, ruff lint (E/F/I/UP/B), pytest with pythonpath=src
D008 Config: TOML + Pydantic v2 with extra="forbid"; unknown keys are hard errors
D009 Relative db_path/logs_dir in config.toml resolve against the config file's directory
D010 All scoring weights and limit-detection patterns are config values, not constants (plan §5.5, §7.2)
D011 Packages are created in the phase that needs them; no empty placeholder modules
D012 Heavy deps (SQLAlchemy, APScheduler, PySide6) are added in their phase, not upfront
D013 DB datetimes are naive UTC; convert to local time only at the UI boundary (domain/clock.py)
D014 Enum columns store enum *values* as TEXT (readable in any SQLite browser), non-native with CHECK
D015 Status changes go through TaskRepository.set_status() only; transitions.py defines legality
D016 Repositories never commit; the caller owns the transaction (enables single-txn reservation, plan §5.4)
D017 Human-facing diagrams live in docs/ARCHITECTURE.md (Korean, Mermaid) and must be updated in the same task that changes tables/states/modules
D018 Reservation uses compare-and-swap UPDATEs (WHERE status='waiting' / current_task_id IS NULL) instead of BEGIN IMMEDIATE; a lost race rolls back and re-selects
D019 agent_states.current_task_id is claimed at reservation time (not run start), so one agent can never hold two reservations; execution/recovery must clear it
D020 Adapters only build commands and classify errors; ProcessRunner owns execution (deviation from plan §6.1 run())
D021 Process liveness uses psutil only; os.kill(pid, 0) is forbidden on Windows because it TerminateProcess-es the target
D022 Phase 4 CLI compatibility floor is Claude Code 2.1.211 and Codex CLI 0.144.3, verified from installed --help/version output
D023 Queue CLI runs never use dangerous bypass flags: Claude uses permission-mode auto; Codex uses approval never inside workspace-write sandbox
D024 Wrapped prompts are sent over stdin, not command arguments, to avoid Windows command-line limits and duplicate prompt storage in task_runs.command
D025 Claude sessions and Codex rollouts are ephemeral for queue work (`--no-session-persistence`, `--ephemeral`)
D026 Implicit configuration uses a stable per-user writable directory (`%LOCALAPPDATA%/AIQueueRunner` on Windows); explicit relative DB/log paths remain config-file-relative
D027 Runtime dependencies target PySide6 6.11.x and APScheduler 3.11.x; Windows builds pin PyInstaller 6.21.0 and use `onedir`
D028 Empty `allowed_project_roots` blocks all execution; a reserved task with an invalid/out-of-policy path transitions to `blocked`
D029 Phase 6 maintenance is exposed as one-shot CLI options (`--backup`, `--export-config`, `--startup`) so it also works from the packaged executable
D030 Windows per-user startup uses the HKCU Run key; development launches use `python -m ai_queue_runner`, while frozen launches call the executable directly
D031 Process recovery identifies an owned process by both PID and exact OS creation time; unreadable or changed identity is never killed automatically
D032 Authentication errors block the task and put the agent in `error`; only usage-limit errors return the task to waiting and put the agent in `cooldown`
D033 Exit-zero CLI output is not matched against generic limit/auth substrings; Claude's structured `rate_limit_event` is authoritative except when its status is `allowed`
D034 Completed-task archival uses a reserved internal tag because v1's eight-state schema has no archive state; the marker is hidden from user tags and stripped on duplication
D035 “Move to top” assigns priority 100 and demotes other waiting priority-100 tasks to 99, preserving the configured 0..100 priority model without a schema-only queue-position field
D036 Exclusive project locking is part of the atomic task-claim UPDATE (`NOT EXISTS` reserved/running same-path row), not only the pre-selection snapshot
D037 Application services expose one canonical method name per operation; the UI does not carry compatibility aliases or signature-inspection fallbacks
D038 Token workload plans use explicit per-batch Claude/Codex budgets; dedicated tasks consume their own budget first, then Any tasks use deterministic largest-first projected-utilization balancing with Claude as the final tie-breaker
D039 `estimated_tokens=0` means unknown for ordinary tasks, but every `plan-batch` task requires a positive estimate; planned Any tasks persist as the selected dedicated policy
D040 Batch enqueue validates the complete workload then commits all tasks once; `dependency_ids` in a batch may reference existing database tasks, not sibling tasks that do not yet have IDs
D041 Any-task fallback stores the current preference on `tasks` and the transition target on the failed `task_run`; the scheduler excludes the previous agent only while the preferred installed agent is available, preserving deterministic CAS reservation without permanently pinning work
D042 Recurrence is a nullable positive day interval (1=daily, 7=weekly); ExecutionService creates the successor in the success transaction, and a unique recurrence_source_task_id makes each completion idempotent
D043 Worktree parallelism is opt-in; CAS marks only lock-conflicting reservations for deterministic `aqr/T{id}` isolation, and preparation failure releases the reservation to preserve exclusive serial fallback
