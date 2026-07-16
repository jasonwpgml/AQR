FROM: codex
TO: claude
TASK: T008
STATUS: completed

CHANGED:
- src/ai_queue_runner/infrastructure/process_runner.py
- tests/integration/test_process_runner.py
- pyproject.toml
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- ..\.venv\Scripts\python -m pip install -e .[dev]
  -> Successfully installed ai-queue-runner-0.1.0 pytest-asyncio-1.4.0
- ..\.venv\Scripts\python -m pytest -q
  -> 90 passed in 2.86s
- ..\.venv\Scripts\python -m ruff check src tests
  -> All checks passed!
- rg -n "shell=True|os.system" src/
  -> no matches

NOTES:
- ProcessRunner uses asyncio.create_subprocess_exec with argument lists only and returns
  non-zero exit codes instead of raising.
- stdout and stderr are drained concurrently through incremental UTF-8 decoders and flushed
  after every chunk, preserving partial logs without deadlocking on large output.
- Timeout and cancellation kill the child and shield process.wait() until cleanup completes;
  cancellation then re-raises asyncio.CancelledError.
- Eight integration tests cover success, non-zero exit, separated stderr, cwd, timeout,
  cancellation, multi-megabyte output, and incremental streaming.

RISKS:
- process.kill() terminates the direct child as explicitly allowed by T008; dedicated descendant
  process-tree handling remains future hardening if an adapter launches grandchildren.
