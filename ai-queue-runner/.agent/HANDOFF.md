FROM: codex
TO: claude
TASK: T031
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md
- README.md
- config.example.toml
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/application/runtime.py
- src/ai_queue_runner/application/scheduler_service.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/application/worktree_service.py
- src/ai_queue_runner/config/defaults.py
- src/ai_queue_runner/config/loader.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/git_worktrees.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/approval_dialog.py
- src/ai_queue_runner/ui/log_viewer.py
- src/ai_queue_runner/ui/main_window.py
- tests/integration/test_git_worktrees.py
- tests/integration/test_repositories.py
- tests/integration/test_stabilization.py
- tests/unit/test_config_loader.py
- tests/unit/test_ui.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 283 passed in 26.16s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> no whitespace errors (Git emitted only existing LF/CRLF conversion warnings)

NOTES:
- Schema v5 adds `tasks.use_worktree/worktree_path/worktree_branch` and matching path/branch fields on `task_runs`; v4 databases migrate idempotently.
- `[scheduler] worktree_parallel_enabled=false` is the opt-in switch. `app.worktrees_dir` and the Git timeout are configurable; relative worktree paths resolve beside the config file.
- The ordinary CAS still requires no reserved/running same-project row. Only an enabled lock-conflicting candidate uses an alternate CAS that requires the lock to exist and marks that reservation for isolation.
- Immediately after reservation, `WorktreeService` creates or reuses `worktrees_dir/T<ID>` on `aqr/T<ID>`. Non-Git projects, conflicting branch/path ownership, and Git failures release task+agent back to waiting, preserving exclusive serial fallback.
- Successful preparation changes only the adapter execution cwd/prompt path. The task's original project path remains stable, while task details, approval preview, log labels, `show-task`, and task-run rows expose the actual path and branch.
- Worktrees survive success, failure, cancellation, stale reservation recovery, and running-task recovery. A persisted isolated task revalidates/reuses the same registered checkout after restart; orphan worktree directories are not scheduler locks.
- `cleanup-worktree ID` accepts terminal tasks, refuses dirty files and branch commits not ancestral to the original project's current HEAD, removes only the registered managed worktree, and leaves the branch plus DB run history. Tasks with live worktrees cannot be deleted or moved to another project before cleanup.
- Removed only the implemented Git-worktree bullet from plan section 20. No automatic merge, conflict resolution, commit, branch deletion, or PR behavior was added.

RISKS:
- Cleanup deliberately rejects squash/rebase-only integrations because the task branch is not an ancestor of project HEAD; users must verify and clean those cases manually.
- Completed branches are intentionally retained and can accumulate until the user manages them.
- The feature has a global opt-in only; there is no user-editable per-task override.
- Pre-existing untracked `.agent/TASK_T031.md` was read as the task source and not modified.
