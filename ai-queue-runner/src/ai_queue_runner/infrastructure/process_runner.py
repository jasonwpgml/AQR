"""Asynchronous subprocess execution with durable file-streamed output."""

from __future__ import annotations

import asyncio
import codecs
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TextIO

from ai_queue_runner.domain.clock import utcnow

_STREAM_CHUNK_SIZE = 64 * 1024


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
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            assert process.stdout is not None
            assert process.stderr is not None

            stdout_task = asyncio.create_task(self._stream(process.stdout, stdout_file))
            stderr_task = asyncio.create_task(self._stream(process.stderr, stderr_file))
            wait_task = asyncio.create_task(process.wait())
            stream_tasks = (stdout_task, stderr_task)
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
                await asyncio.gather(*stream_tasks)
                raise

            await asyncio.gather(*stream_tasks)

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
    async def _kill_and_wait(
        process: asyncio.subprocess.Process,
        wait_task: asyncio.Task[int],
    ) -> None:
        if process.returncode is None:
            try:
                process.kill()
            except ProcessLookupError:
                pass
        await asyncio.shield(wait_task)
