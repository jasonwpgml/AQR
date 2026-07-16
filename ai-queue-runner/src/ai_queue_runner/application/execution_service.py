"""End-to-end execution of one task that has already been reserved."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.adapters.base import (
    AgentAdapter,
    validated_limit_reset,
)
from ai_queue_runner.infrastructure.adapters.base import (
    extract_limit_reset as default_extract_limit_reset,
)
from ai_queue_runner.infrastructure.adapters.base import (
    summarize_output as default_summarize_output,
)
from ai_queue_runner.infrastructure.path_policy import PathPolicyError, validate_project_path
from ai_queue_runner.infrastructure.process_runner import ProcessRunner, ProcessRunResult
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    TaskRepository,
    TaskRunRepository,
)

_OUTPUT_TAIL_BYTES = 4096
_SUMMARY_TAIL_BYTES = 64 * 1024


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

        try:
            canonical_project_path = validate_project_path(
                task.project_path,
                self._config.app.allowed_project_roots,
            )
        except PathPolicyError as exc:
            self._record_invalid_path(session, task, adapter, exc)
            raise
        task.project_path = str(canonical_project_path)
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
                # AIDEV-NOTE: prompts travel over stdin to avoid Windows command-line
                # limits and to keep task text out of the persisted command array.
                stdin_text=adapter.build_stdin(task),
                on_started=lambda pid, process_started_at: self._record_started(
                    session,
                    task,
                    run,
                    pid,
                    process_started_at,
                ),
            )
        except asyncio.CancelledError:
            _populate_result_summary(run, adapter, stdout_path)
            self._record_cancelled(task_repo, task, run, agent_state)
            session.commit()
            raise
        except OSError as exc:
            _populate_result_summary(run, adapter, stdout_path)
            self._record_spawn_failure(task_repo, task, run, agent_state, exc)
            session.commit()
            return run

        _populate_result_summary(run, adapter, stdout_path)
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

    @staticmethod
    def _record_invalid_path(
        session: Session,
        task: Task,
        adapter: AgentAdapter,
        exc: PathPolicyError,
    ) -> None:
        TaskRepository(session).set_status(task, TaskStatus.BLOCKED)
        task.assigned_agent = None
        task.reserved_at = None
        task.last_error = str(exc)
        agent_state = AgentStateRepository(session).get_or_create(adapter.name)
        agent_state.status = AgentStatus.AVAILABLE
        agent_state.current_task_id = None
        session.commit()

    @staticmethod
    def _record_started(
        session: Session,
        task: Task,
        run: TaskRun,
        pid: int,
        started_at,
    ) -> None:
        run.pid = pid
        run.started_at = started_at
        task.started_at = started_at
        session.commit()

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
        output_tail = _read_output_tail(stderr_path, stdout_path)
        classified_error = adapter.classify_error(output_tail, result.exit_code)
        settings = self._config.agents[adapter.name.value]
        matched_unstructured_limit = (
            result.exit_code != 0
            and _matches_usage_limit(output_tail, settings.limit_patterns)
        )

        if (
            result.exit_code == 0
            and classified_error not in {ErrorType.AUTH, ErrorType.USAGE_LIMIT, ErrorType.CRASH}
            and not matched_unstructured_limit
        ):
            task_repo.set_status(task, TaskStatus.SUCCEEDED)
            task.finished_at = result.finished_at
            task.last_error = None
            agent_state.status = AgentStatus.AVAILABLE
            agent_state.current_task_id = None
            agent_state.available_at = None
            agent_state.last_error = None
            return

        if (
            classified_error is ErrorType.USAGE_LIMIT
            or matched_unstructured_limit
        ):
            reset_output = _read_output_tail(
                stderr_path,
                stdout_path,
                max_bytes=_SUMMARY_TAIL_BYTES,
            )
            available_at = _extract_limit_reset(adapter, reset_output)
            if available_at is None:
                available_at = utcnow() + timedelta(
                    minutes=settings.default_cooldown_minutes
                )
            reset_note = (
                "usage limit detected; automatically available at "
                f"{available_at.isoformat(sep=' ', timespec='seconds')} UTC"
            )
            run.error_type = ErrorType.USAGE_LIMIT
            task_repo.set_status(task, TaskStatus.WAITING)
            task.assigned_agent = None
            task.reserved_at = None
            task.started_at = None
            task.last_error = f"{output_tail}\n{reset_note}" if output_tail else reset_note
            agent_state.status = AgentStatus.COOLDOWN
            agent_state.available_at = available_at
            agent_state.current_task_id = None
            agent_state.last_error = task.last_error
            return

        if classified_error is ErrorType.AUTH:
            run.error_type = ErrorType.AUTH
            task_repo.set_status(task, TaskStatus.BLOCKED)
            task.last_error = output_tail or "authentication failed"
            task.finished_at = result.finished_at
            agent_state.status = AgentStatus.ERROR
            agent_state.current_task_id = None
            agent_state.last_error = task.last_error
            return

        error_type = (
            ErrorType.TIMEOUT
            if result.timed_out
            else classified_error
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


def _read_output_tail(
    stderr_path: Path,
    stdout_path: Path,
    *,
    max_bytes: int = _OUTPUT_TAIL_BYTES,
) -> str:
    return "\n".join(
        part
        for path in (stderr_path, stdout_path)
        if (part := _read_tail(path, max_bytes))
    )


def _read_tail(path: Path, max_bytes: int = _OUTPUT_TAIL_BYTES) -> str:
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - max_bytes))
            return stream.read().decode("utf-8", errors="replace").strip()
    except FileNotFoundError:
        return ""


def _populate_result_summary(run: TaskRun, adapter: AgentAdapter, stdout_path: Path) -> None:
    summarizer = getattr(adapter, "summarize_output", default_summarize_output)
    if not callable(summarizer):
        run.result_summary = None
        return
    try:
        run.result_summary = summarizer(_read_tail(stdout_path, _SUMMARY_TAIL_BYTES))
    except Exception:
        run.result_summary = None


def _extract_limit_reset(adapter: AgentAdapter, output: str) -> datetime | None:
    extractor = getattr(adapter, "extract_limit_reset", default_extract_limit_reset)
    if not callable(extractor):
        return None
    try:
        return validated_limit_reset(extractor(output))
    except Exception:
        return None


def _matches_usage_limit(output: str, patterns: list[str]) -> bool:
    folded = output.casefold()
    return any(pattern.casefold() in folded for pattern in patterns)


def _default_failure_message(result: ProcessRunResult) -> str:
    if result.timed_out:
        return "process timed out"
    return f"process exited with code {result.exit_code}"
