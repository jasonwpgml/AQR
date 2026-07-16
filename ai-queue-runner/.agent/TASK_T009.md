[TASK]
ID: T009
Goal: Execution engine — AgentAdapter protocol, ExecutionService that runs a reserved task end-to-end (success / failure / retry / usage-limit / timeout / cancel), and retry promotion. Verified entirely with a FakeAdapter, no real CLI.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md before writing code.
- Building blocks that already exist and MUST be reused, not reimplemented:
  - scheduler_service.reserve_next_task() — produces a `reserved` task with assigned_agent
    and agent_states.current_task_id set (D019).
  - process_runner.ProcessRunner.run(command, cwd, stdout_path, stderr_path,
    timeout_seconds, env) -> ProcessRunResult(pid, exit_code, timed_out, started_at, finished_at).
    Non-zero exits are returned; spawn failure (missing binary) RAISES OSError/FileNotFoundError;
    cancellation re-raises asyncio.CancelledError after killing the child.
  - TaskRepository.set_status() — the only way to change task status (D015).
  - recovery_service.release_stale_reservations() — pattern to follow for the new promotion helper.
- CRITICAL ORDERING (D019 / HANDOFF risk note): the task must be transitioned
  reserved -> running AND committed BEFORE the subprocess is spawned. Otherwise the
  stale-reservation release could hand a started task to another agent (double run).
- Plan references: §6.1 adapter interface, §7.2 usage-limit handling, §8.5 PID recording,
  scenario D (retry) in §15.

[SCOPE]
May create:
- src/ai_queue_runner/infrastructure/adapters/__init__.py
- src/ai_queue_runner/infrastructure/adapters/base.py
- src/ai_queue_runner/application/execution_service.py
- tests/fakes.py
- tests/integration/test_execution_service.py
May modify:
- src/ai_queue_runner/application/recovery_service.py  (ONLY add promote_retry_tasks)
- src/ai_queue_runner/config/defaults.py and config.example.toml
  (ONLY add run_timeout_minutes to AgentSection, default 120, ge=1)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md, .agent/DECISIONS.md (append D020, see below)
Must NOT modify:
- src/ai_queue_runner/domain/**, scheduler_service.py, database.py, repositories.py,
  process_runner.py, logging_config.py, loader.py, main.py, docs/**, README.md,
  existing tests, pyproject.toml

[REQUIREMENTS]
1. adapters/base.py — `AgentAdapter` Protocol (plan §6.1, simplified):
   - `name: AgentName`
   - `def is_installed(self) -> bool`
   - `def build_command(self, task: Task) -> list[str]`
   - `def classify_error(self, output: str, exit_code: int | None) -> ErrorType`
   Note: unlike plan §6.1 there is NO adapter.run(); ProcessRunner executes commands.
   Append to .agent/DECISIONS.md:
   "D020 Adapters only build commands and classify errors; ProcessRunner owns execution (deviation from plan §6.1 run())".
2. execution_service.py — `class ExecutionService` (constructed with config and a ProcessRunner):
   `async def execute(self, session, task: Task, adapter: AgentAdapter) -> TaskRun`
   Preconditions (raise ValueError otherwise): task.status is RESERVED and
   task.assigned_agent == adapter.name.
   a. Start phase (single commit BEFORE spawn): set_status reserved->running,
      task.started_at=utcnow(), agent state status=BUSY, create TaskRun row with
      attempt=task.retry_count+1, command from adapter.build_command(task),
      stdout/stderr paths, started_at. COMMIT. Only then spawn.
   b. Log files: {config.app.logs_dir}/tasks/{task_id}/attempt_{attempt}.stdout.log and
      .stderr.log; store both paths on the TaskRun row.
   c. Run via ProcessRunner with cwd=task.project_path and
      timeout_seconds = config.agents[adapter.name].run_timeout_minutes * 60.
   d. Outcome handling (update TaskRun pid/exit_code/finished_at in all cases, then COMMIT):
      - exit_code == 0: set_status running->succeeded, task.finished_at set,
        agent status=AVAILABLE, current_task_id=None.
      - usage-limit detected (case-insensitive substring match of any
        config.agents[adapter.name].limit_patterns against the LAST ~4096 chars of
        stderr then stdout files): plan §7.2 — set_status running->waiting,
        assigned_agent=None, reserved_at=None, started_at=None, retry_count UNCHANGED,
        task.last_error set; agent status=COOLDOWN, available_at=None (user enters it),
        agent last_error set; TaskRun.error_type=USAGE_LIMIT.
      - other failure (non-zero exit or timed_out): error_type = TIMEOUT if timed_out
        else adapter.classify_error(output_tail, exit_code); retry_count += 1;
        if retry_count <= max_retries: set_status running->retry_wait and
        not_before = utcnow() + retry_delay_minutes; else set_status running->failed.
        task.last_error and finished_at set; agent status=AVAILABLE, current_task_id=None.
      - spawn failure (OSError/FileNotFoundError from ProcessRunner): same as "other
        failure" with error_type=CRASH; TaskRun keeps pid=None, exit_code=None.
      - asyncio.CancelledError: record TaskRun (error_type=CANCELLED, finished_at),
        set_status running->failed (state machine has no running->cancelled),
        task.last_error="cancelled by user", agent status=AVAILABLE,
        current_task_id=None, COMMIT, then RE-RAISE.
   e. Never log or store env contents or secrets (plan §8.4).
3. recovery_service.promote_retry_tasks(session, now=None) -> list[Task]:
   tasks in RETRY_WAIT with (not_before is None or not_before <= now) go back to WAITING
   via set_status. Owns its commit, mirrors release_stale_reservations style.
4. tests/fakes.py — `FakeAdapter` implementing the protocol with a configurable
   command (default: [sys.executable, "-c", <script>]) and a simple substring-based
   classify_error. Keep it reusable for Phase 4/5 tests.

[TESTS]
tests/integration/test_execution_service.py (pytest-asyncio is configured, asyncio_mode=auto).
Build each case with a real SQLite tmp DB + reserve_next_task + FakeAdapter scripts:
1. success: exit 0 -> succeeded; TaskRun has pid/exit_code 0/log paths; both log files
   exist with expected content; agent AVAILABLE with current_task_id=None
2. failure with retries remaining -> retry_wait, retry_count==1, not_before ≈ now+delay
   (assert within a tolerance), last_error set
3. failure with max_retries=0 -> failed
4. usage-limit: script prints "usage limit reached" to stderr, exits 1 -> task WAITING,
   retry_count unchanged, assigned_agent None; agent COOLDOWN; TaskRun.error_type USAGE_LIMIT
5. timeout: script sleeps 30s, run_timeout_minutes tiny (use a config override with a
   fractional value is not allowed — int ge=1 — so monkeypatch the computed seconds or
   construct config then patch; keep the test < 5s) -> error_type TIMEOUT, retry path
6. spawn failure: FakeAdapter command ["definitely-not-a-real-binary-xyz"] -> failed/retry
   path with error_type CRASH, pid None
7. cancellation: cancel the execute() task mid-run -> CancelledError propagates,
   task FAILED with error_type CANCELLED recorded on the TaskRun, agent AVAILABLE
8. promote_retry_tasks: backdated not_before -> WAITING; future not_before stays RETRY_WAIT
9. full pipeline: register 1 any-task -> reserve_next_task -> execute -> succeeded,
   and the agent can immediately reserve a second task afterwards
10. precondition: calling execute on a WAITING task raises ValueError

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green (existing 90 + new), ruff clean
- rg -n "shell=True|os.system" src/ returns nothing
- No status assignment outside TaskRepository.set_status (rg -n "\.status =" src/ai_queue_runner/application/ must only show agent-state changes, not task.status)
- ai-anchors rule followed (rg -n "AIDEV-" before editing; anchors only for invisible constraints)

[HANDOFF]
When done: mark T009 [done] in .agent/TASKS.md and add
"[todo] T009b Phase 3: PID-based crash recovery for `running` tasks found at startup (plan §8.5)"
right after it; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "T009b crash recovery"); overwrite .agent/HANDOFF.md in the standard format.
