from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

import psutil
import pytest

from ai_queue_runner.infrastructure.process_runner import (
    ProcessIdentityError,
    ProcessRunner,
    get_process_started_at,
    kill_process_tree,
)


def python_command(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_identity_checked_kill_refuses_different_process_start() -> None:
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started_at = get_process_started_at(process.pid)
        assert started_at is not None

        with pytest.raises(ProcessIdentityError, match="different process"):
            kill_process_tree(
                process.pid,
                expected_started_at=started_at - timedelta(days=1),
            )

        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=10)


async def run_process(
    tmp_path: Path,
    code: str,
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
    stdin_text: str | None = None,
):
    return await ProcessRunner().run(
        python_command(code),
        cwd=cwd or tmp_path,
        stdout_path=tmp_path / "logs" / "stdout.log",
        stderr_path=tmp_path / "logs" / "stderr.log",
        timeout_seconds=timeout_seconds,
        stdin_text=stdin_text,
    )


@pytest.mark.asyncio
async def test_success_streams_stdout(tmp_path: Path) -> None:
    result = await run_process(tmp_path, "print('completed')")

    assert result.exit_code == 0
    assert result.timed_out is False
    assert result.pid is not None
    assert result.started_at <= result.finished_at
    assert (tmp_path / "logs" / "stdout.log").read_text(encoding="utf-8") == "completed\n"


@pytest.mark.asyncio
async def test_nonzero_exit_code_is_returned(tmp_path: Path) -> None:
    result = await run_process(tmp_path, "import sys; sys.exit(3)")

    assert result.exit_code == 3
    assert result.timed_out is False
    assert result.pid is not None


@pytest.mark.asyncio
async def test_stderr_is_separate_from_stdout(tmp_path: Path) -> None:
    result = await run_process(
        tmp_path,
        "import sys; sys.stdout.write('stdout'); sys.stderr.write('stderr')",
    )

    assert result.exit_code == 0
    assert (tmp_path / "logs" / "stdout.log").read_text(encoding="utf-8") == "stdout"
    assert (tmp_path / "logs" / "stderr.log").read_text(encoding="utf-8") == "stderr"


@pytest.mark.asyncio
async def test_large_stdin_is_delivered_without_command_line_limits(tmp_path: Path) -> None:
    content = "x" * (128 * 1024)

    result = await run_process(
        tmp_path,
        "import sys; print(len(sys.stdin.read()))",
        stdin_text=content,
    )

    assert result.exit_code == 0
    assert (tmp_path / "logs" / "stdout.log").read_text(encoding="utf-8") == "131072\n"


@pytest.mark.asyncio
async def test_cwd_is_respected(tmp_path: Path) -> None:
    working_directory = tmp_path / "project"
    working_directory.mkdir()

    result = await run_process(
        tmp_path,
        "import os; print(os.getcwd())",
        cwd=working_directory,
    )

    assert result.exit_code == 0
    reported_cwd = (tmp_path / "logs" / "stdout.log").read_text(encoding="utf-8").strip()
    assert Path(reported_cwd).resolve() == working_directory.resolve()


@pytest.mark.asyncio
async def test_timeout_kills_process_and_returns_quickly(tmp_path: Path) -> None:
    loop = asyncio.get_running_loop()
    started = loop.time()

    result = await run_process(
        tmp_path,
        "import time; time.sleep(30)",
        timeout_seconds=1,
    )

    assert loop.time() - started < 5
    assert result.timed_out is True
    assert result.pid is not None
    assert result.exit_code is not None


@pytest.mark.asyncio
async def test_cancellation_kills_process_before_propagating(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[asyncio.subprocess.Process] = []
    original_create_subprocess_exec = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        process = await original_create_subprocess_exec(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)
    task = asyncio.create_task(run_process(tmp_path, "import time; time.sleep(30)"))

    await wait_until(lambda: bool(processes))
    assert processes[0].returncode is None
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    assert processes[0].returncode is not None


@pytest.mark.asyncio
async def test_started_callback_receives_live_pid_before_completion(tmp_path: Path) -> None:
    started = asyncio.Event()
    observed: list[tuple[int, bool]] = []

    def on_started(pid, _started_at):
        observed.append((pid, psutil.pid_exists(pid)))
        started.set()

    task = asyncio.create_task(
        ProcessRunner().run(
            python_command("import time; time.sleep(30)"),
            cwd=tmp_path,
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            on_started=on_started,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=5)

    assert observed and observed[0][1] is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_started_callback_failure_reaps_spawned_process(tmp_path: Path) -> None:
    observed_pid = None

    def fail_after_start(pid, _started_at):
        nonlocal observed_pid
        observed_pid = pid
        raise RuntimeError("persistence callback failed")

    with pytest.raises(RuntimeError, match="persistence callback failed"):
        await ProcessRunner().run(
            python_command("import time; time.sleep(30)"),
            cwd=tmp_path,
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            on_started=fail_after_start,
        )

    assert observed_pid is not None
    assert not psutil.pid_exists(observed_pid)


@pytest.mark.asyncio
async def test_process_may_close_stdin_before_large_prompt_finishes(tmp_path: Path) -> None:
    result = await run_process(
        tmp_path,
        "pass",
        stdin_text="x" * (3 * 1024 * 1024),
    )

    assert result.exit_code == 0


@pytest.mark.asyncio
async def test_cancellation_kills_descendant_processes(tmp_path: Path) -> None:
    child_code = "import time; time.sleep(30)"
    parent_code = (
        "import subprocess, sys, time; "
        f"child=subprocess.Popen([sys.executable, '-c', {child_code!r}]); "
        "print(child.pid, flush=True); time.sleep(30)"
    )
    task = asyncio.create_task(run_process(tmp_path, parent_code))
    stdout_path = tmp_path / "logs" / "stdout.log"
    await wait_until(
        lambda: stdout_path.exists()
        and bool(stdout_path.read_text(encoding="utf-8").strip())
    )
    child_pid = int(stdout_path.read_text(encoding="utf-8").strip())
    assert psutil.pid_exists(child_pid)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)

    await wait_until(lambda: not psutil.pid_exists(child_pid))


@pytest.mark.asyncio
async def test_large_output_does_not_deadlock(tmp_path: Path) -> None:
    output_size = 3 * 1024 * 1024

    result = await run_process(
        tmp_path,
        f"import sys; sys.stdout.write('x' * {output_size}); sys.stdout.flush()",
        timeout_seconds=10,
    )

    assert result.exit_code == 0
    assert result.timed_out is False
    assert (tmp_path / "logs" / "stdout.log").stat().st_size == output_size


@pytest.mark.asyncio
async def test_output_is_streamed_while_process_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    processes: list[asyncio.subprocess.Process] = []
    original_create_subprocess_exec = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        process = await original_create_subprocess_exec(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)
    task = asyncio.create_task(
        run_process(
            tmp_path,
            "import sys, time; print('marker', flush=True); time.sleep(30)",
        )
    )
    stdout_path = tmp_path / "logs" / "stdout.log"

    try:
        await wait_until(
            lambda: stdout_path.exists()
            and "marker" in stdout_path.read_text(encoding="utf-8"),
        )
        assert processes[0].returncode is None
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def wait_until(predicate, timeout_seconds: float = 5) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not predicate():
        if loop.time() >= deadline:
            pytest.fail("condition was not met before the deadline")
        await asyncio.sleep(0.01)
