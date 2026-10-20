# AQR test and verification summary

AI Queue Runner has no benchmark dataset. What this directory records instead is
what the automated test suite checks, the result of one full run, and the
reliability defaults the code ships with, so anyone can rerun it and compare.
All files here are licensed under [CC BY 4.0](LICENSE): you may copy, adapt and
redistribute them, including commercially, **as long as you credit the source**
(see [How to cite](#how-to-cite)).

## Contents

| Path | What it is | Format |
|---|---|---|
| [`test-summary.json`](test-summary.json) | One full run of the suite on 2026-10-21: environment, pass/fail counts, tests per file, statement coverage per layer, ruff result | JSON |

## Latest run

Windows 10 (10.0.19045), Python 3.12.4, PySide6 6.11.2, pytest 9.1.1, ruff 0.17.1.

| Measure | Result |
|---|---|
| Tests collected / passed | 372 / 372 (0 failed, 0 skipped) |
| Unit / integration tests | 200 / 172 |
| Statement coverage (`ai_queue_runner`) | 88% (6186 statements, 733 missed) |
| `ruff check src tests` | All checks passed |
| Wall time | 91.6 s |

One warning was reported: a `PytestUnraisableExceptionWarning` from the Windows
signal wakeup file descriptor in `test_worker.py`. The test itself passes.

### Tests by area

Grouped by test file. Per-file counts are in `test-summary.json`.

| Area | Test files | Tests |
|---|---|---|
| Scheduling and atomic reservation | `test_scheduler`, `test_scoring`, `test_task_reservation`, `test_workload_planning` | 49 |
| Task states and persistence | `test_transitions`, `test_repositories`, `test_task_provenance` | 47 |
| Execution, retries, recovery, worker | `test_execution_service`, `test_process_runner`, `test_recovery`, `test_runtime`, `test_worker`, `test_runtime_owner`, `test_soak` | 72 |
| Agent CLI adapters and usage-limit detection | `test_adapters` | 46 |
| Git worktrees, auto-commit, draft PRs | `test_git_worktrees`, `test_auto_commit`, `test_draft_pr` | 26 |
| Services, CLI, config, templates, backup | `test_application_services`, `test_main`, `test_config_loader`, `test_path_policy`, `test_prompt_templates`, `test_task_templates`, `test_log_rendering`, `test_stabilization` | 86 |
| Desktop UI (Qt offscreen) | `test_ui` | 46 |
| **Total** | 27 files | **372** |

### Coverage by layer

| Layer | Statements | Missed | Coverage |
|---|---|---|---|
| `domain/` | 248 | 0 | 100.0% |
| `config/` | 135 | 2 | 98.5% |
| `application/` | 2562 | 236 | 90.8% |
| `infrastructure/` | 1227 | 125 | 89.8% |
| `ui/` | 1679 | 295 | 82.4% |
| entry points (`main.py`, `__main__.py`, `__init__.py`) | 335 | 75 | 77.6% |

## What the tests do and do not cover

- Agents are replaced by fake adapters that run short Python child processes,
  so no test calls the real Claude Code, Codex or Gemini CLI or uses tokens.
  Adapter tests check the exact command lines, output parsing and usage-limit
  classification against recorded output shapes.
- Git tests create throwaway repositories in a temporary directory.
- The soak test (`test_soak.py`) queues 20 tasks, restarts the runtime after 10
  have finished, and checks that all 20 succeed with exactly 20 runs and no
  duplicate execution. `scripts/run_soak.ps1` repeats it for a set number of
  hours (8 by default); that long run is not part of this summary.
- Real CLI runs are documented in the project docs, not measured here: the app
  README notes that the Codex and Claude Code success paths were checked in
  non-interactive mode, and the AI guide records an end-to-end unattended run
  with the real Codex CLI. Gemini ships disabled until its CLI contract is
  verified.

## Reliability defaults

These are the shipped defaults from `config/defaults.py`, `config.example.toml`
and `application/task_service.py`. All are configurable.

| Policy | Default | Where |
|---|---|---|
| Retries per task | `max_retries = 2`, `retry_delay_minutes = 10` (fail, wait 10 min, retry; third failure is final) | task |
| Usage limit hit | Task returns to `waiting` without using a retry; agent goes to cooldown | runtime |
| Cooldown when the CLI gives no reset time | `default_cooldown_minutes = 300` | `[agents.*]` |
| Run timeout | `run_timeout_minutes = 120`, then the whole process tree is killed | `[agents.*]` |
| Stale reservation release | `reserved_timeout_minutes = 10` | `[scheduler]` |
| Queue poll interval | `poll_interval_seconds = 30` | `[scheduler]` |
| `any`-task fallback to another agent | after `fallback_failure_threshold = 1` retryable failure; immediately on a usage limit | `[scheduler]` |
| Git / `gh` command timeouts | 30 s for worktree, auto-commit and draft-PR commands | `[scheduler]` |
| Same-project concurrency | one running task per project (`exclusive`); parallel git worktrees only when `worktree_parallel_enabled = true` | `[scheduler]` |
| Scoring | dedicated-agent bonus 20, +1 per waiting hour (max 24 h), -5 per retry, +2 per dependent (max 10); dedicated tasks get +10 after 12 h of waiting, and dedicated tasks at priority 90+ are considered before `any` tasks | `[scheduler]` |
| Usage-limit patterns | `usage limit`, `rate limit`, `rate_limit`, `session limit` (Gemini adds `resource exhausted`, `quota will reset` and others) | `[agents.*]` |

## Reproducing

From the repository root on Windows:

```powershell
py -3.12 -m venv .venv
cd ai-queue-runner
..\.venv\Scripts\python -m pip install -e ".[dev]"
..\.venv\Scripts\python -m ruff check src tests
..\.venv\Scripts\python -m pytest -q --cov=ai_queue_runner --cov-report=term
..\.venv\Scripts\python -m pytest --collect-only -q   # per-file counts
```

Wall time depends on the machine; counts and pass results should match for the
same commit.

## How to cite

Credit is required for any reuse of this material. Use GitHub's **Cite this
repository** button (from [`CITATION.cff`](../CITATION.cff)), or:

> Jason (@jasonwpgml). *AI Queue Runner (AQR): a local task queue that runs
> coding work through Claude Code, Codex and Gemini CLI around usage limits*,
> verification data. https://github.com/jasonwpgml/AQR

```bibtex
@software{aqr2026,
  author  = {Jason (jasonwpgml)},
  title   = {AI Queue Runner (AQR): a local task queue that runs coding work through Claude Code, Codex and Gemini CLI around usage limits},
  year    = {2026},
  version = {0.1.0},
  url     = {https://github.com/jasonwpgml/AQR},
  license = {Apache-2.0 (code), CC-BY-4.0 (data)}
}
```
