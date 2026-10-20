# AI Queue Runner (AQR)

**English** | [한국어](README.ko.md)

**An AI coding agent task queue for Windows: line up work for Claude Code, Codex and Gemini CLI, and let it run on its own when usage limits reset.**

AI Queue Runner (AQR) is an open-source desktop app and headless worker that schedules coding tasks for AI coding agent CLIs: **Claude Code**, **OpenAI Codex CLI** and **Gemini CLI**. You queue tasks with a priority, a target agent and dependencies. AQR picks the next task whenever an agent is free, puts an agent into cooldown when it hits a **usage limit** or rate limit, and resumes after the reset. It can also run several tasks on the same repository in parallel **git worktrees**, auto-commit the results and open draft pull requests.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![CI](https://github.com/jasonwpgml/AQR/actions/workflows/ci.yml/badge.svg)](https://github.com/jasonwpgml/AQR/actions/workflows/ci.yml)
![Python 3.12-3.14](https://img.shields.io/badge/python-3.12--3.14-blue.svg)
![Windows](https://img.shields.io/badge/platform-Windows-lightgrey.svg)

> If AQR is useful to you, please **star the repo**. It helps other people find it.

---

## Why

Coding agents have usage windows. When Claude Code or Codex runs out, you wait for the reset, and by then you have forgotten what was next or which agent should do it. AQR keeps that list for you and keeps the agents busy:

- **Queue now, run later.** Register tasks at any time; they run when an agent becomes available, including overnight.
- **Usage limits are expected, not failures.** A limit puts the agent into cooldown and the task back in the queue without spending a retry. An `any` task moves to another free agent.
- **Nothing runs twice, nothing collides.** Reservation is atomic in SQLite, each agent runs one task at a time, and one project runs one task at a time unless you opt in to worktrees.
- **You stay in control.** Tasks only run inside folders you allow, and tasks marked `confirm` wait for your approval.

## Features

- **Agents:** Claude Code (`claude`), OpenAI Codex CLI (`codex`) and Gemini CLI (`gemini`, opt-in). Each task targets one agent or `any`.
- **Scheduling:** priority 0-100, dependencies between tasks, a `not-before` time, recurring tasks (daily, weekly, every N days), tags, and a score that rewards waiting time and dedicated tasks and penalizes retries.
- **Usage-limit handling:** limit detection from CLI output (Claude's reported reset time when available, otherwise a configurable cooldown, 300 minutes by default), automatic return to `available` after the reset, and fallback of `any` tasks to another agent.
- **Retries and recovery:** per-task retries with delay (default 2 retries, 10 minutes apart), run timeouts that kill the whole process tree, release of stale reservations, and recovery of tasks left running after a crash.
- **Parallel git worktrees (opt-in):** a task that conflicts with a running task on the same repository runs in `worktrees/T<ID>` on branch `aqr/T<ID>`. Nothing is merged automatically.
- **Auto-commit and draft PRs (opt-in):** commit a successful run's changes, and push a managed worktree branch as a GitHub draft PR through `gh`.
- **Desktop UI (PySide6):** task table, agent status panel, approval dialog, logs, calendar with daily summaries, system tray and notifications.
- **Headless worker:** `worker` runs the queue without the GUI. The GUI can still open alongside it to watch progress.
- **CLI for other agents:** every queue operation is a subcommand with `--json` output, so Claude Code or Codex can add, inspect, split and review tasks themselves. `plan-batch` spreads a batch across agents by token budget.
- **Maintenance:** SQLite online backup, config export, start with Windows, PyInstaller build script.

## How it works

```
  You / an AI agent                     AQR                                  Agent CLIs
 ───────────────────      ──────────────────────────────────────      ──────────────────
  GUI or CLI          ──►  SQLite queue (tasks, dependencies,     ──►  claude  (Claude Code)
  add-task, plan-batch     agent states, runs, events)                 codex   (Codex CLI)
                                     │                                 gemini  (Gemini CLI)
                           scheduler tick (every 30 s)                       │
                           filter: agent available, deps done,               │ output, exit code
                                   project free, not-before passed           ▼
                           score + atomic reservation          ◄──  succeeded / failed / usage limit
                                     │                                 → retry, cooldown, fallback
                           run in project folder or git worktree
                           → optional auto-commit → optional draft PR
```

Details: [architecture](ai-queue-runner/docs/ARCHITECTURE.md) (Korean), [requirements and plan](AI_QUEUE_RUNNER_PLAN.md) (Korean).

## Quick start

Requirements: Windows, Python 3.12-3.14, Git, and the CLI of each agent you want to use (`claude`, `codex`, optionally `gemini`) installed and signed in.

> The desktop UI and the CLI's plain-text messages are currently in Korean. Add `--json` to any command for language-neutral output.

```powershell
git clone https://github.com/jasonwpgml/AQR.git
cd AQR
py -3.12 -m venv .venv
cd ai-queue-runner
..\.venv\Scripts\python -m pip install -e .

# Copy the example config and set allowed_project_roots (empty = nothing runs)
Copy-Item config.example.toml config.toml

# Start the desktop app
..\.venv\Scripts\python -m ai_queue_runner --config config.toml
```

Minimal `config.toml` changes for unattended runs:

```toml
[app]
global_auto_run = true
allowed_project_roots = ["C:/projects"]
```

A new database starts with every agent in `unknown` state. Mark the agents you use as available once, in the GUI's agent panel or from the CLI:

```powershell
..\.venv\Scripts\python -m ai_queue_runner --config config.toml set-agent claude available
..\.venv\Scripts\python -m ai_queue_runner --config config.toml set-agent codex available
```

### Add tasks from the command line

```powershell
# Register a task that runs without asking (approval defaults to confirm)
..\.venv\Scripts\python -m ai_queue_runner --config config.toml add-task `
  --title "Add regression tests" --prompt "Add regression tests for the reservation flow." `
  --project "C:\projects\sample" --agent any --priority 80 --approval auto

# Run only after a local time
..\.venv\Scripts\python -m ai_queue_runner --config config.toml add-task `
  --title "Docs check" --prompt "Compare the README with the real commands." `
  --project "C:\projects\sample" --not-before "2026-11-02 09:00" --approval auto

# Inspect the queue as JSON
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json list-tasks --status waiting
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json queue-status
```

### Run without the GUI

```powershell
..\.venv\Scripts\python -m ai_queue_runner --config config.toml worker
```

The worker runs only `auto` tasks. A file lock lets one scheduler (worker or GUI) own a database at a time; the GUI can still open in watch mode.

The full command reference (Korean) is in [`ai-queue-runner/README.md`](ai-queue-runner/README.md), and the rules for AI agents driving the queue are in [`ai-queue-runner/docs/AI_GUIDE.md`](ai-queue-runner/docs/AI_GUIDE.md).

## Configuration highlights

| Setting | Default | Purpose |
|---|---|---|
| `[app] allowed_project_roots` | empty | Tasks outside these folders never run |
| `[app] global_auto_run` | `false` | When `false`, every task asks for approval |
| `[scheduler] poll_interval_seconds` | `30` | Scheduler tick |
| `[scheduler] worktree_parallel_enabled` | `false` | Same-repository parallel runs in git worktrees |
| `[scheduler] auto_commit_enabled` | `false` | Allow auto-commit (also needs `--auto-commit` on the task) |
| `[scheduler] draft_pr_enabled` | `false` | Allow draft PRs (also needs `--draft-pr` on the task) |
| `[agents.<name>] default_cooldown_minutes` | `300` | Cooldown when the CLI reports a limit without a reset time |
| `[agents.<name>] run_timeout_minutes` | `120` | Kill the run after this long |
| `[agents.<name>] limit_patterns` | see example | Text that marks a usage-limit error |

See [`config.example.toml`](ai-queue-runner/config.example.toml) for every option.

## Safety model

- **Allowed folders only.** A task's project must be an existing absolute path under `allowed_project_roots`.
- **Approval by default.** Tasks default to `confirm`, and `global_auto_run` defaults to `false`.
- **Agent permissions.** Claude Code runs with `--permission-mode auto`, Codex with `--sandbox workspace-write --ask-for-approval never`, and Gemini with `--approval-mode yolo --sandbox`. Agents can change files in the project folder without asking, so only allow folders you are comfortable handing to an agent, and use Git.
- **Prompts on stdin.** Prompts are not passed as command-line arguments.
- **Git never changes a result.** Auto-commit is skipped when the repository was dirty before the run, and a failed commit or PR does not turn a successful task into a failure.
- **Local only, no telemetry.** AQR talks only to the agent CLIs, `git` and `gh` on your machine.

## Verification

The suite runs on every push in CI (`windows-latest`): `ruff check src tests` and `pytest`. The latest recorded run is 372 tests passed with 88% statement coverage. Test counts by area, coverage by layer and the shipped retry, cooldown and timeout defaults are in [`experiments/`](experiments/README.md).

## FAQ

**How do I run Claude Code tasks automatically when my usage limit resets?**
Queue the tasks with `--approval auto`, set `global_auto_run = true`, mark Claude as available, and keep the GUI scheduler or `worker` running. When Claude reports a limit, AQR puts it into cooldown until the reset time and then continues the queue.

**Can I share one task queue between Claude Code and Codex?**
Yes. Give a task `--agent any` and the scheduler gives it to whichever agent is free. If one agent hits its limit, the task moves to the other.

**Can several AI agents work on the same repository at the same time?**
Only if you opt in. With `worktree_parallel_enabled = true`, a conflicting task runs in its own git worktree and branch (`aqr/T<ID>`). Otherwise one task runs per project at a time.

**Can an AI agent add tasks to the queue itself?**
Yes. Every operation is a CLI subcommand with `--json` output, and `--actor`/`--actor-session` record which agent and session created or changed a task.

**Does it work on macOS or Linux?**
Not yet. AQR is built and tested on Windows (process handling, startup registration and the build script are Windows-specific).

**Is Gemini CLI supported?**
The adapter is included but ships disabled (`[agents.gemini] enabled = false`) until its CLI contract is verified on your machine.

## Using AQR in your own project

You are welcome to use, modify and redistribute AQR, commercially or not, under the [Apache License 2.0](LICENSE). The one thing asked in return is credit:

- Keep the [`NOTICE`](NOTICE) file with any copy or derivative work, as the license requires.
- Mention the project in your README or docs, for example:

  > Built on [AI Queue Runner (AQR)](https://github.com/jasonwpgml/AQR) by Jason ([@jasonwpgml](https://github.com/jasonwpgml)).

- GitHub's **"Cite this repository"** button uses [`CITATION.cff`](CITATION.cff).
- The verification data in [`experiments/`](experiments/) is CC BY 4.0 and needs the same credit.

And if it helped, a star is always appreciated.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md). Run `ruff check src tests` and `pytest -q` from `ai-queue-runner/` before opening a PR.

## License

[Apache-2.0](LICENSE) © 2026 Jason ([@jasonwpgml](https://github.com/jasonwpgml)). See [NOTICE](NOTICE). Third-party components, including PySide6/Qt (LGPLv3), are listed in [THIRD_PARTY_NOTICES.md](ai-queue-runner/THIRD_PARTY_NOTICES.md).
