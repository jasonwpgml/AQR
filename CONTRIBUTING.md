# Contributing to AI Queue Runner

Thanks for helping. Issues, ideas and pull requests are all welcome. Writing in English or Korean is fine.

## Development

AQR is Windows-only. You need Python 3.12, 3.13 or 3.14. The virtual environment lives at the repository root and commands run from `ai-queue-runner/`, as in CI:

```powershell
py -3.12 -m venv .venv
cd ai-queue-runner
..\.venv\Scripts\python -m pip install -e ".[dev]"

..\.venv\Scripts\python -m ruff check src tests
..\.venv\Scripts\python -m pytest -q
```

The tests use fake agent adapters, temporary SQLite databases and throwaway Git repositories. They never call the real Claude Code, Codex or Gemini CLIs (Git must be installed), and the UI tests run headless with Qt's `offscreen` platform.

## Layout

| Path | What it does |
|---|---|
| `ai-queue-runner/src/ai_queue_runner/domain/` | Task states, transitions, scoring, SQLAlchemy models |
| `ai-queue-runner/src/ai_queue_runner/application/` | Use cases: task CRUD, scheduler, execution, recovery, worktrees, batch planning, CLI |
| `ai-queue-runner/src/ai_queue_runner/infrastructure/` | SQLite, process runner, Claude/Codex/Gemini adapters, Git, path policy |
| `ai-queue-runner/src/ai_queue_runner/ui/` | PySide6 desktop UI |
| `ai-queue-runner/tests/` | `unit/` and `integration/` tests |
| `ai-queue-runner/docs/` | Architecture and the AI operations guide |

Read [`ai-queue-runner/docs/ARCHITECTURE.md`](ai-queue-runner/docs/ARCHITECTURE.md) before changing scheduling, reservation or recovery. AI agents should also follow [`AGENTS.md`](AGENTS.md).

## Pull requests

- Keep the safety rules: nothing runs outside `allowed_project_roots`, one task is never reserved twice, and Git failures never change a task's result.
- Add or update tests for behavior changes. Run `ruff check src tests` and `pytest -q` before opening a PR; CI runs the same on `windows-latest`.
- If you change tables, states or module boundaries, update `docs/ARCHITECTURE.md` in the same PR.

By contributing you agree that your contribution is licensed under the Apache License 2.0, the same as the project.
