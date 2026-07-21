# AGENTS.md — AI Queue Runner

Instructions for AI agents working in this repository.
Claude acts as orchestrator/reviewer; Codex acts as implementer/tester.

## Read first, in this order

1. `docs/PLAN.md` — full requirements and architecture (Korean). It is the source of truth.
2. `.agent/STATE.md` — current phase, active task, owner.
3. `.agent/TASKS.md` — task backlog and status.
4. `.agent/DECISIONS.md` — settled decisions; do not relitigate them.
5. `.agent/HANDOFF.md` — the most recent handoff between agents.

## Roles

- **Claude**: decides WHAT / WHY / BOUNDARY. Breaks work into small tasks, writes task
  instructions (plan §18), reviews diffs and test results (plan §19), integrates, updates docs.
- **Codex**: decides HOW / CODE / TEST. Implements only the files listed in the task SCOPE,
  adds unit tests, runs them, reports results in the HANDOFF format.

## Working rules

- Modify only files inside the task's SCOPE. If a change outside SCOPE seems required,
  stop and report it instead of doing it.
- Before starting a task: set `owner` in `.agent/STATE.md`. After finishing: write `.agent/HANDOFF.md`.
- One task at a time; a task should be one feature, one layer, or 3–6 files (plan §12.3).
- Do NOT guess the Claude/Codex CLI command formats. Adapters are finalized in Phase 4
  only after inspecting the installed CLIs' `--help` output.
- No unrequested refactoring, no speculative abstraction. Exactly two adapters (claude, codex) in v1.
- Duplicate-execution prevention beats automation: task selection + reservation must happen
  in a single DB transaction (plan §5.4).
- Security: pass subprocess arguments as a list with `shell=False`; never log API keys,
  tokens, or full environment variables.

## Commands (run from `ai-queue-runner/`)

```
../.venv/Scripts/python -m pip install -e .[dev]   # one-time setup (venv lives at repo root)
../.venv/Scripts/python -m pytest -q               # tests
../.venv/Scripts/python -m ruff check src tests    # lint
../.venv/Scripts/python -m ai_queue_runner         # run the app
```

Every task must end with a green `pytest` run; report the exact command and result.

## Conventions

- Python 3.12+, src layout, line length 100, ruff rules E/F/I/UP/B.
- Pydantic v2 models with `extra="forbid"` for anything user-editable.
- Tunables (scoring weights, limit patterns, timeouts) go in `config/defaults.py`
  and `config.example.toml`, never hardcoded at call sites.
- Tests: `tests/unit/` for pure logic, `tests/integration/` for DB/process flows.
- Anchor comments: before editing, search `rg -n "AIDEV-"` in the target scope.
  Use `AIDEV-NOTE:` / `AIDEV-TODO:` / `AIDEV-QUESTION:` only for constraints that are
  not visible in the code itself; update or remove stale anchors after editing.
- Domain invariants are enforced in code, not documentation: task status changes go
  through `TaskRepository.set_status()` (never assign `task.status` directly), datetimes
  come from `domain/clock.utcnow()` (never `datetime.now()`), repositories never commit.
- `docs/ARCHITECTURE.md` holds the human-facing Mermaid diagrams (Korean). Any task that
  adds/changes a table, a task status, or a top-level module must update those diagrams
  and the file map in the same task.

## Handoff format

When finishing a task, overwrite `.agent/HANDOFF.md` with:

```
FROM / TO / TASK / STATUS
CHANGED:  list of files
VERIFIED: exact commands + results
NOTES:    implementation details the reviewer must know
RISKS:    anything unresolved
```
