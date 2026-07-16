[TASK]
ID: T008
Goal: Implement an asyncio-based ProcessRunner that spawns CLI subprocesses with file-streamed output, timeout, and cancellation — the foundation of the Phase 3 execution engine.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md before writing code.
- Scheduler core is done: reservation (scheduler_service.reserve_next_task) and stale-reservation
  recovery exist. ProcessRunner is pure infrastructure: it must know NOTHING about Task/DB/config.
  The execution service (T009) will wire it to tasks later.
- Relevant decisions: D013 (naive UTC via ai_queue_runner.domain.clock.utcnow), plan §8.4
  (subprocess security), §8.5 (PID must be recorded for crash recovery).
- This machine is Windows 11; tests must pass on Windows. Use sys.executable for test subprocesses.

[SCOPE]
May modify:
- src/ai_queue_runner/infrastructure/process_runner.py   (new)
- tests/integration/test_process_runner.py               (new)
- pyproject.toml  (ONLY to add pytest-asyncio>=0.23 to the dev extra and its
  asyncio config under [tool.pytest.ini_options])
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- src/ai_queue_runner/domain/**, application/**, config/**
- existing infrastructure files (database.py, repositories.py, logging_config.py)
- docs/, README.md, config.example.toml, tests other than the new file

[REQUIREMENTS]
1. `ProcessRunResult` (frozen dataclass): pid: int | None, exit_code: int | None,
   timed_out: bool, started_at: datetime, finished_at: datetime. Times come from
   ai_queue_runner.domain.clock.utcnow().
2. `class ProcessRunner` with:
   `async def run(self, command: list[str], cwd: Path, stdout_path: Path, stderr_path: Path,
    timeout_seconds: float | None = None, env: dict[str, str] | None = None) -> ProcessRunResult`
3. Spawn with asyncio.create_subprocess_exec ONLY. Never a shell string, never shell=True,
   never os.system (plan §8.4).
4. stdout/stderr are streamed to the given files WHILE the process runs (create parent dirs;
   UTF-8, errors="replace"), so partial logs survive a crash mid-run. Reading both pipes must
   not deadlock on large output.
5. timeout_seconds elapsed -> kill the process tree (process.kill() is acceptable),
   wait for exit, return with timed_out=True and whatever exit_code resulted.
6. If the awaiting asyncio task is cancelled, the subprocess must be killed before
   CancelledError propagates — no orphan processes. (Cancellation re-raises; it does not
   return a result.)
7. Non-zero exit codes are RETURNED, not raised. pid is set in the result whenever the
   process started, including failed/timed-out runs.
8. Do not log, print, or write env contents anywhere (plan §8.4).

[TESTS]
tests/integration/test_process_runner.py using pytest-asyncio and sys.executable -c snippets:
1. success: exit 0, expected text lands in the stdout file
2. non-zero exit code is returned (e.g. sys.exit(3))
3. stderr goes to the stderr file, separate from stdout
4. cwd is respected (child prints its os.getcwd())
5. timeout: child sleeps 30s with timeout_seconds~1 -> returns quickly, timed_out=True
6. cancellation: cancel the asyncio task while child sleeps -> CancelledError raised and
   the child process is no longer running (poll via psutil-free check: process handle's
   returncode set after kill/wait)
7. large output: child prints several MB to stdout -> completes, no deadlock, file size matches
8. incremental streaming: child prints a marker, flushes, then sleeps long; poll the stdout
   file (with a deadline) until the marker appears while the child still runs; then cancel

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pip install -e .[dev]
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green on Windows (existing 82 tests + new ones), ruff clean
- rg -n "shell=True|os.system" src/ returns nothing
- ai-anchors rule followed: rg -n "AIDEV-" on touched scope before editing; add anchors only
  for constraints invisible in code; no stale anchors left

[HANDOFF]
When done: mark T008 [done] in .agent/TASKS.md, set .agent/STATE.md
(owner: codex, status: done, last_verified with the real pytest result,
next: "T009 FakeAdapter + execution service"), and overwrite .agent/HANDOFF.md
in the standard format (CHANGED / VERIFIED / NOTES / RISKS).
