"""Asynchronous subprocess execution with durable file-streamed output."""

from __future__ import annotations

import asyncio
import codecs
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

import psutil

from ai_queue_runner.domain.clock import utcnow

_STREAM_CHUNK_SIZE = 64 * 1024


class ProcessIdentityError(RuntimeError):
    """Raised when a PID no longer belongs to the process expected by the caller."""


@dataclass(frozen=True)
class ProcessRunResult:
    """Outcome metadata for a subprocess that was started by ``ProcessRunner``."""

    pid: int | None
    exit_code: int | None
    timed_out: bool
    started_at: datetime
    finished_at: datetime


class ProcessRunner:
    """Run one command without a shell and stream its output to separate files."""

    async def run(
        self,
        command: list[str],
        cwd: Path,
        stdout_path: Path,
        stderr_path: Path,
        timeout_seconds: float | None = None,
        env: dict[str, str] | None = None,
        stdin_text: str | None = None,
        on_started: Callable[[int, datetime], None] | None = None,
    ) -> ProcessRunResult:
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)

        started_at = utcnow()
        with (
            stdout_path.open("w", encoding="utf-8", errors="replace", newline="") as stdout_file,
            stderr_path.open("w", encoding="utf-8", errors="replace", newline="") as stderr_file,
        ):
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd,
                env=env,
                stdin=(
                    asyncio.subprocess.PIPE
                    if stdin_text is not None
                    else asyncio.subprocess.DEVNULL
                ),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            started_at = get_process_started_at(process.pid) or started_at
            if on_started is not None:
                try:
                    on_started(process.pid, started_at)
                except BaseException:
                    process.kill()
                    await process.wait()
                    raise
            assert process.stdout is not None
            assert process.stderr is not None

            stdin_task = (
                asyncio.create_task(self._write_stdin(process.stdin, stdin_text))
                if stdin_text is not None and process.stdin is not None
                else None
            )
            stdout_task = asyncio.create_task(self._stream(process.stdout, stdout_file))
            stderr_task = asyncio.create_task(self._stream(process.stderr, stderr_file))
            wait_task = asyncio.create_task(process.wait())
            stream_tasks = (stdout_task, stderr_task)
            io_tasks = (*stream_tasks, *((stdin_task,) if stdin_task is not None else ()))
            timed_out = False

            try:
                if timeout_seconds is None:
                    await asyncio.shield(wait_task)
                else:
                    try:
                        await asyncio.wait_for(asyncio.shield(wait_task), timeout_seconds)
                    except TimeoutError:
                        timed_out = True
                        await self._kill_and_wait(process, wait_task)
            except asyncio.CancelledError:
                await self._kill_and_wait(process, wait_task)
                await asyncio.gather(*io_tasks, return_exceptions=True)
                raise

            await asyncio.gather(*io_tasks)

        return ProcessRunResult(
            pid=process.pid,
            exit_code=process.returncode,
            timed_out=timed_out,
            started_at=started_at,
            finished_at=utcnow(),
        )

    @staticmethod
    async def _stream(reader: asyncio.StreamReader, destination: TextIO) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        while chunk := await reader.read(_STREAM_CHUNK_SIZE):
            destination.write(decoder.decode(chunk))
            destination.flush()

        remaining = decoder.decode(b"", final=True)
        if remaining:
            destination.write(remaining)
            destination.flush()

    @staticmethod
    async def _write_stdin(writer: asyncio.StreamWriter, content: str) -> None:
        try:
            writer.write(content.encode("utf-8"))
            await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

    @staticmethod
    async def _kill_and_wait(
        process: asyncio.subprocess.Process,
        wait_task: asyncio.Task[int],
    ) -> None:
        if process.returncode is None:
            await asyncio.to_thread(kill_process_tree, process.pid)
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
        await asyncio.shield(wait_task)


def kill_process_tree(pid: int, *, expected_started_at: datetime | None = None) -> None:
    """Kill a process and all descendants, tolerating concurrent exits."""
    try:
        parent = psutil.Process(pid)
        if expected_started_at is not None:
            actual_started_at = _process_started_at(parent)
            if actual_started_at is None:
                raise ProcessIdentityError(f"cannot verify process identity for PID {pid}")
            if actual_started_at != expected_started_at:
                raise ProcessIdentityError(f"PID {pid} belongs to a different process")
        processes = [*parent.children(recursive=True), parent]
    except psutil.NoSuchProcess:
        return

    for process in processes:
        try:
            process.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


def get_process_started_at(pid: int) -> datetime | None:
    """Return the OS process creation time as naive UTC, if it is readable."""
    try:
        process = psutil.Process(pid)
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    return _process_started_at(process)


def _process_started_at(process: psutil.Process) -> datetime | None:
    try:
        created_at = process.create_time()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        return None
    return datetime.fromtimestamp(created_at, tz=UTC).replace(tzinfo=None)
