[TASK]
ID: T009b
Goal: PID-based crash recovery — at startup, detect tasks left in `running` by a previous
app session and let the caller recover each one to failed or waiting (plan §8.5).

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md before writing code.
- ExecutionService records a TaskRun per attempt with `pid` and leaves the task `running`
  if the app dies mid-run. On restart nothing currently cleans this up.
- The state machine allows running->failed and running->waiting (transitions.py).
- Plan §8.5: the USER chooses failed-or-waiting per task; therefore this task ships a
  detect function + a recover function with an explicit disposition parameter. No UI yet —
  the Phase 5 UI and the future headless loop will call these.
- CRITICAL Windows constraint: os.kill(pid, 0) TERMINATES the target process on Windows
  (any sig other than CTRL_* calls TerminateProcess). NEVER use os.kill for liveness.
  Use psutil instead (add dependency).

[SCOPE]
May modify:
- src/ai_queue_runner/application/recovery_service.py  (add the new functions)
- pyproject.toml  (ONLY add "psutil>=5.9" to [project] dependencies)
- tests/integration/test_recovery.py  (extend with the new tests)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md, .agent/DECISIONS.md (append D021, see below)
Must NOT modify:
- src/ai_queue_runner/domain/**, execution_service.py, scheduler_service.py,
  infrastructure/**, config/**, main.py, docs/**, README.md, config.example.toml,
  other tests

[REQUIREMENTS]
1. `@dataclass(frozen=True) class OrphanedRun:` task_id: int, run_id: int | None,
   pid: int | None, pid_alive: bool.
2. `find_orphaned_running_tasks(session) -> list[OrphanedRun]`
   - every task with status RUNNING is orphaned by definition when this is called at
     startup (the caller guarantees no executor is active — document this contract in
     the docstring, do not try to detect it).
   - pid comes from the task's latest TaskRun (highest attempt); may be None.
   - pid_alive via psutil.pid_exists(pid) (False when pid is None). Read-only: this
     function must not mutate anything (rollback before returning is fine).
3. `recover_orphaned_task(session, task: Task, *, to_status: TaskStatus,
    kill_if_alive: bool = False) -> None`
   - to_status must be FAILED or WAITING, else ValueError.
   - If the latest run's pid is still alive: raise RuntimeError unless kill_if_alive=True,
     in which case terminate it via psutil.Process(pid).kill() (ignore NoSuchProcess),
     and wait for exit with a short timeout (psutil Process.wait(timeout=10)).
   - Close the dangling TaskRun: finished_at=utcnow(), error_type=ErrorType.CRASH
     (leave exit_code None).
   - Task: set_status to the chosen status; last_error="recovered after crash";
     finished_at set when FAILED; when WAITING also clear assigned_agent, reserved_at,
     started_at (retry_count UNCHANGED — a crash is not the task's fault).
   - Agent state whose current_task_id points at this task: current_task_id=None,
     status=AgentStatus.UNKNOWN (the user re-confirms availability after a crash).
   - Owns its commit, mirroring the existing recovery functions.
4. Append to .agent/DECISIONS.md:
   "D021 Process liveness uses psutil only; os.kill(pid, 0) is forbidden on Windows because it TerminateProcess-es the target".
   Also add an AIDEV-NOTE stating the os.kill constraint next to the psutil usage.

[TESTS]
Extend tests/integration/test_recovery.py (reuse its fixtures/helpers; drive a task to
`running` via reserve_next_task + set_status, insert a TaskRun row directly):
1. dead pid: spawn `sys.executable -c "pass"` with subprocess.run to get a real but
   exited pid -> find_orphaned_running_tasks reports pid_alive=False
2. recover to WAITING: task waiting, assigned_agent/reserved_at/started_at cleared,
   retry_count unchanged, TaskRun closed with CRASH, agent UNKNOWN with
   current_task_id=None
3. recover to FAILED: task failed with finished_at and last_error set
4. alive pid: spawn `sys.executable -c "import time; time.sleep(60)"` with
   subprocess.Popen -> pid_alive=True; recover without kill_if_alive raises RuntimeError;
   with kill_if_alive=True the child dies and recovery completes (ensure cleanup in
   finally so no sleeper leaks even on assert failure)
5. invalid to_status (e.g. SUCCEEDED) raises ValueError
6. tasks not in RUNNING are not reported
7. pid None (no TaskRun row): reported with pid_alive=False and recoverable

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pip install -e .[dev]   (pulls psutil)
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green (existing 100 + new), ruff clean
- rg -n "os\.kill" src/ returns nothing
- No task.status assignment outside TaskRepository.set_status
- ai-anchors rule followed

[HANDOFF]
When done: mark T009b [done] in .agent/TASKS.md; set .agent/STATE.md (owner: codex,
status: done, real pytest result, next: "T010 — inspect installed claude/codex CLI
--help; finalize command formats"); overwrite .agent/HANDOFF.md in the standard format.
