FROM: codex
TO: claude
TASK: T039
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/DECISIONS.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/TASK_T039.md
- .agent/HANDOFF.md
- README.md
- config.example.toml
- docs/AI_GUIDE.md
- docs/ARCHITECTURE.md
- src/ai_queue_runner/config/defaults.py
- src/ai_queue_runner/domain/models.py
- src/ai_queue_runner/infrastructure/database.py
- src/ai_queue_runner/infrastructure/draft_pr.py
- src/ai_queue_runner/application/execution_service.py
- src/ai_queue_runner/application/task_service.py
- src/ai_queue_runner/application/task_template_service.py
- src/ai_queue_runner/application/recurrence_service.py
- src/ai_queue_runner/application/cli_service.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/ui/main_window.py
- tests/unit/test_draft_pr.py
- tests/unit/test_config_loader.py
- tests/unit/test_main.py
- tests/unit/test_ui.py
- tests/integration/test_auto_commit.py
- tests/integration/test_repositories.py
- tests/integration/test_task_templates.py

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 341 passed in 21.24s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `git diff --check` -> clean (line-ending warnings only)
- Local `gh 2.96.0` help confirmed the used `pr list` and `pr create` flags; no network or live GitHub command was run.

NOTES:
- Schema v11 adds tasks/task_templates.draft_pr and task_runs.draft_pr_url/draft_pr_result. The v10 migration checks each column before ALTER and is retry-safe.
- Registration, update, GUI drafts, and templates reject draft_pr=true unless auto_commit=true. CLI supports --draft-pr/--no-draft-pr, templates persist the flag, and recurrence/duplicate/rework paths preserve it.
- Publication requires scheduler.draft_pr_enabled plus task.draft_pr and an actual auto-commit hash. Missing auto-commit records a skip without invoking GitHub commands.
- Push policy is worktree-only: the recorded and checked-out branch must equal aqr/T<ID>. Non-worktree runs never push the user's current branch and record a skip reason.
- The injected command runner checks origin, gh availability/authentication, and any existing PR across all states. Existing URLs are reused; otherwise it pushes origin and calls gh pr create --draft with task/run/agent/commit metadata.
- Publication URL or skip/failure detail is exposed in show-task JSON/text and the minimal GUI detail panel. Every publication outcome is non-fatal after SUCCEEDED is committed.
- The implemented draft PR item was removed from plan section 20. No Git commit was created.

RISKS:
- Only lock-conflicting tasks currently receive managed worktrees, so a draft-PR-enabled task that runs in the primary checkout intentionally skips publication.
- Live remote push/GitHub behavior was not exercised by tests; all git/gh publication calls use injected fakes as required.
- A process crash between push/PR creation and DB persistence can leave external state ahead of task_runs; the all-state existing-PR lookup makes a later retry converge without creating another PR.
- The packaged executable must be rebuilt and restarted before its local database and UI move to schema v11.
