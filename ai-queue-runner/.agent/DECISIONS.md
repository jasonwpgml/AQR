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
