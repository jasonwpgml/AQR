from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from ai_queue_runner.infrastructure.process_runner import ProcessRunner


def python_command(code: str) -> list[str]:
    return [sys.executable, "-c", code]


async def run_process(
    tmp_path: Path,
    code: str,
    *,
    cwd: Path | None = None,
    timeout_seconds: float | None = None,
):
    return await ProcessRunner().run(
        python_command(code),
        cwd=cwd or tmp_path,
        stdout_path=tmp_path / "logs" / "stdout.log",
        stderr_path=tmp_path / "logs" / "stderr.log",
        timeout_seconds=timeout_seconds,
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
