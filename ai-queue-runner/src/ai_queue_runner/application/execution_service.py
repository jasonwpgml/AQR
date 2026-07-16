"""End-to-end execution of one task that has already been reserved."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.adapters.base import AgentAdapter
from ai_queue_runner.infrastructure.process_runner import ProcessRunner, ProcessRunResult
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    TaskRepository,
    TaskRunRepository,
)

_OUTPUT_TAIL_BYTES = 4096


class ExecutionService:
    """Run a reserved task and persist every terminal outcome."""

    def __init__(self, config: AppConfig, process_runner: ProcessRunner) -> None:
        self._config = config
        self._process_runner = process_runner

    async def execute(
        self,
        session: Session,
        task: Task,
        adapter: AgentAdapter,
    ) -> TaskRun:
        if task.status is not TaskStatus.RESERVED:
            raise ValueError("task must be reserved before execution")
        if task.assigned_agent != adapter.name:
            raise ValueError("reserved task is assigned to a different agent")

        command = adapter.build_command(task)
        attempt = task.retry_count + 1
        task_log_dir = self._config.app.logs_dir / "tasks" / str(task.id)
        stdout_path = task_log_dir / f"attempt_{attempt}.stdout.log"
        stderr_path = task_log_dir / f"attempt_{attempt}.stderr.log"
        started_at = utcnow()

        task_repo = TaskRepository(session)
        agent_state = AgentStateRepository(session).get_or_create(adapter.name)
        task_repo.set_status(task, TaskStatus.RUNNING)
        task.started_at = started_at
        agent_state.status = AgentStatus.BUSY
        run = TaskRunRepository(session).add(
            TaskRun(
                task_id=task.id,
                agent=adapter.name,
                attempt=attempt,
                command=command,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started_at,
            )
        )
        # AIDEV-NOTE: this commit must precede process spawn; stale-reservation
        # recovery may otherwise release a task whose CLI process already started.
        session.commit()

        try:
            result = await self._process_runner.run(
                command,
                cwd=Path(task.project_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=self._timeout_seconds(adapter),
                env=None,
            )
        except asyncio.CancelledError:
            self._record_cancelled(task_repo, task, run, agent_state)
            session.commit()
            raise
        except OSError as exc:
            self._record_spawn_failure(task_repo, task, run, agent_state, exc)
            session.commit()
            return run

        self._record_result(
            task_repo,
            task,
            run,
            agent_state,
            adapter,
            result,
            stdout_path,
            stderr_path,
        )
        session.commit()
        return run

    def _timeout_seconds(self, adapter: AgentAdapter) -> float:
        return self._config.agents[adapter.name.value].run_timeout_minutes * 60

    def _record_result(
        self,
        task_repo: TaskRepository,
        task: Task,
        run: TaskRun,
        agent_state,
        adapter: AgentAdapter,
        result: ProcessRunResult,
        stdout_path: Path,
        stderr_path: Path,
    ) -> None:
        run.pid = result.pid
        run.exit_code = result.exit_code
        run.finished_at = result.finished_at

        if result.exit_code == 0:
            task_repo.set_status(task, TaskStatus.SUCCEEDED)
            task.finished_at = result.finished_at
            agent_state.status = AgentStatus.AVAILABLE
            agent_state.current_task_id = None
            return

        output_tail = _read_output_tail(stderr_path, stdout_path)
        settings = self._config.agents[adapter.name.value]
        if _matches_usage_limit(output_tail, settings.limit_patterns):
            run.error_type = ErrorType.USAGE_LIMIT
            task_repo.set_status(task, TaskStatus.WAITING)
            task.assigned_agent = None
            task.reserved_at = None
            task.started_at = None
            task.last_error = output_tail or "usage limit detected"
            agent_state.status = AgentStatus.COOLDOWN
            agent_state.available_at = None
            agent_state.current_task_id = None
            agent_state.last_error = task.last_error
            return

        error_type = (
            ErrorType.TIMEOUT
            if result.timed_out
            else adapter.classify_error(output_tail, result.exit_code)
        )
        error_message = output_tail or _default_failure_message(result)
        self._record_failure(
            task_repo,
            task,
            run,
            agent_state,
            error_type,
            error_message,
            result.finished_at,
        )

    @staticmethod
    def _record_spawn_failure(
        task_repo: TaskRepository,
        task: Task,
        run: TaskRun,
        agent_state,
        exc: OSError,
    ) -> None:
        finished_at = utcnow()
        run.pid = None
        run.exit_code = None
        run.finished_at = finished_at
        ExecutionService._record_failure(
            task_repo,
            task,
            run,
            agent_state,
            ErrorType.CRASH,
            f"{type(exc).__name__}: {exc}",
            finished_at,
        )

    @staticmethod
    def _record_cancelled(
        task_repo: TaskRepository,
        task: Task,
        run: TaskRun,
        agent_state,
    ) -> None:
        finished_at = utcnow()
        run.pid = None
        run.exit_code = None
        run.finished_at = finished_at
        run.error_type = ErrorType.CANCELLED
        task_repo.set_status(task, TaskStatus.FAILED)
        task.last_error = "cancelled by user"
        task.finished_at = finished_at
        agent_state.status = AgentStatus.AVAILABLE
        agent_state.current_task_id = None

    @staticmethod
    def _record_failure(
        task_repo: TaskRepository,
        task: Task,
        run: TaskRun,
        agent_state,
        error_type: ErrorType,
        error_message: str,
        finished_at,
    ) -> None:
        run.error_type = error_type
        task.retry_count += 1
        if task.retry_count <= task.max_retries:
            task_repo.set_status(task, TaskStatus.RETRY_WAIT)
            task.not_before = utcnow() + timedelta(minutes=task.retry_delay_minutes)
        else:
            task_repo.set_status(task, TaskStatus.FAILED)
        task.last_error = error_message
        task.finished_at = finished_at
        agent_state.status = AgentStatus.AVAILABLE
        agent_state.current_task_id = None


def _read_output_tail(stderr_path: Path, stdout_path: Path) -> str:
    return "\n".join(part for path in (stderr_path, stdout_path) if (part := _read_tail(path)))


def _read_tail(path: Path) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - _OUTPUT_TAIL_BYTES))
            return stream.read().decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return ""


def _matches_usage_limit(output: str, patterns: list[str]) -> bool:
    folded = output.casefold()
    return any(pattern.casefold() in folded for pattern in patterns)


def _default_failure_message(result: ProcessRunResult) -> str:
    if result.timed_out:
        return "process timed out"
    return f"process exited with code {result.exit_code}"
