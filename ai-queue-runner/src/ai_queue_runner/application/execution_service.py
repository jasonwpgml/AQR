"""End-to-end execution of one task that has already been reserved."""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy.orm import Session

from ai_queue_runner.application.recurrence_service import enqueue_next_occurrence
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ErrorType,
    TaskStatus,
)
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.adapters.base import (
    AgentAdapter,
    validated_limit_reset,
)
from ai_queue_runner.infrastructure.adapters.base import (
    extract_limit_reset as default_extract_limit_reset,
)
from ai_queue_runner.infrastructure.adapters.base import (
    extract_limit_warning_reset as default_extract_limit_warning_reset,
)
from ai_queue_runner.infrastructure.adapters.base import (
    summarize_output as default_summarize_output,
)
from ai_queue_runner.infrastructure.draft_pr import DraftPrPublisher, DraftPrResult
from ai_queue_runner.infrastructure.git_auto_commit import (
    AutoCommitPreflight,
    AutoCommitResult,
    GitAutoCommitter,
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
_LAST_ERROR_MAX_CHARS = 1200
_LONG_ENCODED_VALUE = re.compile(
    r"(?<![\w+/=_-])[A-Za-z0-9+/=_-]{160,}(?![\w+/=_-])"
)
_RETRYABLE_FALLBACK_ERRORS = frozenset(
    {ErrorType.CRASH, ErrorType.TIMEOUT, ErrorType.UNKNOWN}
)
logger = logging.getLogger(__name__)


class ExecutionService:
    """Run a reserved task and persist every terminal outcome."""

    def __init__(
        self,
        config: AppConfig,
        process_runner: ProcessRunner,
        auto_committer: GitAutoCommitter | None = None,
        draft_pr_publisher: DraftPrPublisher | None = None,
    ) -> None:
        self._config = config
        self._process_runner = process_runner
        self._auto_committer = auto_committer or GitAutoCommitter(
            timeout_seconds=config.scheduler.auto_commit_git_timeout_seconds
        )
        self._draft_pr_publisher = draft_pr_publisher or DraftPrPublisher(
            timeout_seconds=config.scheduler.draft_pr_command_timeout_seconds
        )

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
        execution_path = canonical_project_path
        if task.use_worktree:
            if not task.worktree_path or not task.worktree_branch:
                raise ValueError("reserved worktree task has no prepared worktree metadata")
            execution_path = Path(task.worktree_path).resolve()
            managed_root = self._config.app.worktrees_dir.resolve()
            if not execution_path.is_dir() or not execution_path.is_relative_to(managed_root):
                raise ValueError(f"prepared worktree path is unavailable: {execution_path}")

        original_project_path = task.project_path
        task.project_path = str(execution_path)
        try:
            command = adapter.build_command(task)
            stdin_text = adapter.build_stdin(task)
        finally:
            task.project_path = original_project_path
        # AIDEV-NOTE: this snapshot must precede agent spawn, including in reused
        # worktrees, so an older task's changes can never be swept into this commit.
        auto_commit_preflight = self._auto_commit_preflight(task, execution_path)
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
        run_repo = TaskRunRepository(session)
        run = run_repo.add(
            TaskRun(
                task_id=task.id,
                agent=adapter.name,
                attempt=attempt,
                command=command,
                stdout_path=str(stdout_path),
                stderr_path=str(stderr_path),
                started_at=started_at,
                worktree_path=(str(execution_path) if task.use_worktree else None),
                worktree_branch=(task.worktree_branch if task.use_worktree else None),
            )
        )
        # AIDEV-NOTE: this commit must precede process spawn; stale-reservation
        # recovery may otherwise release a task whose CLI process already started.
        session.commit()
        if run.worktree_path and run.worktree_branch:
            logger.info(
                "task %s running in worktree: branch=%s path=%s",
                task.id,
                run.worktree_branch,
                run.worktree_path,
            )

        try:
            result = await self._process_runner.run(
                command,
                cwd=execution_path,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                timeout_seconds=self._timeout_seconds(adapter),
                env=None,
                # AIDEV-NOTE: prompts travel over stdin to avoid Windows command-line
                # limits and to keep task text out of the persisted command array.
                stdin_text=stdin_text,
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
            self._record_spawn_failure(
                task_repo,
                run_repo,
                task,
                run,
                agent_state,
                exc,
            )
            session.commit()
            return run

        _populate_result_summary(run, adapter, stdout_path)
        self._record_result(
            session,
            task_repo,
            run_repo,
            task,
            run,
            agent_state,
            adapter,
            result,
            stdout_path,
            stderr_path,
        )
        session.commit()
        draft_pr_requested = self._config.scheduler.draft_pr_enabled and task.draft_pr
        if task.status is TaskStatus.SUCCEEDED and (
            auto_commit_preflight is not None or draft_pr_requested
        ):
            if auto_commit_preflight is not None:
                self._record_auto_commit(
                    task,
                    run,
                    adapter,
                    execution_path,
                    auto_commit_preflight,
                )
            self._record_draft_pr(task, run, adapter, execution_path)
            session.commit()
        return run

    def _auto_commit_preflight(
        self,
        task: Task,
        execution_path: Path,
    ) -> AutoCommitPreflight | None:
        if not (self._config.scheduler.auto_commit_enabled and task.auto_commit):
            return None
        return self._auto_committer.preflight(execution_path)

    def _record_auto_commit(
        self,
        task: Task,
        run: TaskRun,
        adapter: AgentAdapter,
        execution_path: Path,
        preflight: AutoCommitPreflight,
    ) -> None:
        try:
            result = self._auto_committer.commit(
                execution_path,
                preflight,
                title=task.title,
                task_id=task.id,
                run_id=run.id,
                agent=adapter.name.value,
            )
        except Exception as exc:
            detail = " ".join(f"{type(exc).__name__}: {exc}".split())[:500]
            result = AutoCommitResult(
                None,
                f"failed: {detail or 'unexpected auto-commit error'}",
            )
            logger.exception("task %s automatic commit crashed", task.id)
        run.auto_commit_hash = result.commit_hash
        run.auto_commit_result = result.detail
        logger.info(
            "task %s automatic commit result: %s%s",
            task.id,
            result.detail,
            f" ({result.commit_hash})" if result.commit_hash else "",
        )

    def _record_draft_pr(
        self,
        task: Task,
        run: TaskRun,
        adapter: AgentAdapter,
        execution_path: Path,
    ) -> None:
        if not (self._config.scheduler.draft_pr_enabled and task.draft_pr):
            return
        if run.auto_commit_hash is None:
            run.draft_pr_result = (
                "skipped: automatic commit was not created"
                + (f" ({run.auto_commit_result})" if run.auto_commit_result else "")
            )
            return
        try:
            result = self._draft_pr_publisher.publish(
                execution_path,
                branch=run.worktree_branch,
                title=task.title,
                task_id=task.id,
                run_id=run.id,
                agent=adapter.name.value,
                commit_hash=run.auto_commit_hash,
            )
        except Exception as exc:
            detail = " ".join(f"{type(exc).__name__}: {exc}".split())[:500]
            result = DraftPrResult(
                None,
                f"failed: {detail or 'unexpected draft PR error'}",
            )
            logger.exception("task %s draft PR publishing crashed", task.id)
        run.draft_pr_url = result.url
        run.draft_pr_result = result.detail
        logger.info(
            "task %s draft PR result: %s%s",
            task.id,
            result.detail,
            f" ({result.url})" if result.url else "",
        )

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
        session: Session,
        task_repo: TaskRepository,
        run_repo: TaskRunRepository,
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
        settings = self._config.agents[adapter.name.value]

        # AIDEV-NOTE: a normal zero exit is authoritative even when successful JSONL
        # contains advisory limit/auth text; post-processing must never discard its work.
        if result.exit_code == 0:
            task.finished_at = result.finished_at
            task_repo.set_status(task, TaskStatus.SUCCEEDED)
            task.last_error = None
            task.fallback_agent = None
            # AIDEV-NOTE: success and its successor share this transaction; the
            # unique source-task key makes replay safe after a crash or restart.
            enqueue_next_occurrence(
                session,
                task,
                completed_at=result.finished_at,
            )
            agent_state.current_task_id = None
            agent_state.last_error = None
            warning_output = _read_output_tail(
                stderr_path,
                stdout_path,
                max_bytes=_SUMMARY_TAIL_BYTES,
            )
            warning_reset = _extract_limit_warning_reset(adapter, warning_output)
            if warning_reset is None:
                agent_state.status = AgentStatus.AVAILABLE
                agent_state.available_at = None
            else:
                agent_state.status = AgentStatus.COOLDOWN
                agent_state.available_at = warning_reset
            return

        classified_error = adapter.classify_error(output_tail, result.exit_code)
        matched_unstructured_limit = _matches_usage_limit(
            output_tail, settings.limit_patterns
        )
        error_summary = _summarize_error_output(output_tail)

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
            fallback_note = self._record_fallback(
                task,
                run,
                reason="usage limit",
            )
            task_repo.set_status(task, TaskStatus.WAITING)
            task.assigned_agent = None
            task.reserved_at = None
            task.started_at = None
            task.last_error = _join_notes(error_summary, reset_note, fallback_note)
            agent_state.status = AgentStatus.COOLDOWN
            agent_state.available_at = available_at
            agent_state.current_task_id = None
            agent_state.last_error = task.last_error
            return

        if classified_error is ErrorType.AUTH:
            run.error_type = ErrorType.AUTH
            task_repo.set_status(task, TaskStatus.BLOCKED)
            task.last_error = error_summary or "authentication failed"
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
        error_message = error_summary or _default_failure_message(result)
        self._record_failure(
            task_repo,
            run_repo,
            task,
            run,
            agent_state,
            error_type,
            error_message,
            result.finished_at,
        )

    def _record_spawn_failure(
        self,
        task_repo: TaskRepository,
        run_repo: TaskRunRepository,
        task: Task,
        run: TaskRun,
        agent_state,
        exc: OSError,
    ) -> None:
        finished_at = utcnow()
        attempted_executable = run.command[0] if run.command else "알 수 없음"
        run.pid = None
        run.exit_code = None
        run.finished_at = finished_at
        self._record_failure(
            task_repo,
            run_repo,
            task,
            run,
            agent_state,
            ErrorType.CRASH,
            "CLI를 시작할 수 없습니다. "
            f"시도한 실행 파일: {attempted_executable}. "
            "설정의 executable 경로와 CLI 설치 상태를 확인하세요. "
            f"({type(exc).__name__}: {exc})",
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

    def _record_failure(
        self,
        task_repo: TaskRepository,
        run_repo: TaskRunRepository,
        task: Task,
        run: TaskRun,
        agent_state,
        error_type: ErrorType,
        error_message: str,
        finished_at,
    ) -> None:
        run.error_type = error_type
        task.retry_count += 1
        fallback_note = None
        if task.retry_count <= task.max_retries:
            task_repo.set_status(task, TaskStatus.RETRY_WAIT)
            task.not_before = utcnow() + timedelta(minutes=task.retry_delay_minutes)
            if (
                error_type in _RETRYABLE_FALLBACK_ERRORS
                and self._consecutive_failures(run_repo, task.id, run.agent)
                >= self._config.scheduler.fallback_failure_threshold
            ):
                fallback_note = self._record_fallback(
                    task,
                    run,
                    reason=(
                        f"{self._config.scheduler.fallback_failure_threshold} "
                        "consecutive failures"
                    ),
                )
        else:
            task_repo.set_status(task, TaskStatus.FAILED)
        task.last_error = _join_notes(error_message, fallback_note)
        task.finished_at = finished_at
        agent_state.status = AgentStatus.AVAILABLE
        agent_state.current_task_id = None

    def _record_fallback(
        self,
        task: Task,
        run: TaskRun,
        *,
        reason: str,
    ) -> str | None:
        if (
            not self._config.scheduler.fallback_enabled
            or task.agent_policy is not AgentPolicy.ANY
        ):
            return None
        fallback_agent = next(
            (
                candidate
                for candidate in AgentName
                if candidate is not run.agent
                and (settings := self._config.agents.get(candidate.value)) is not None
                and settings.enabled
            ),
            None,
        )
        if fallback_agent is None:
            return None
        task.fallback_agent = fallback_agent
        run.fallback_to_agent = fallback_agent
        note = f"fallback scheduled: {run.agent.value} -> {fallback_agent.value} ({reason})"
        logger.info("task %s %s", task.id, note)
        return note

    @staticmethod
    def _consecutive_failures(
        run_repo: TaskRunRepository,
        task_id: int,
        agent: AgentName,
    ) -> int:
        count = 0
        for previous in reversed(run_repo.list_for_task(task_id)):
            if (
                previous.agent is not agent
                or previous.error_type not in _RETRYABLE_FALLBACK_ERRORS
            ):
                break
            count += 1
        return count


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


def _extract_limit_warning_reset(adapter: AgentAdapter, output: str) -> datetime | None:
    extractor = getattr(
        adapter,
        "extract_limit_warning_reset",
        default_extract_limit_warning_reset,
    )
    if not callable(extractor):
        return None
    try:
        return validated_limit_reset(extractor(output))
    except Exception:
        return None


def _matches_usage_limit(output: str, patterns: list[str]) -> bool:
    folded = output.casefold()
    return any(pattern.casefold() in folded for pattern in patterns)


def _join_notes(*parts: str | None) -> str:
    return _truncate_last_error("\n".join(part for part in parts if part))


def _summarize_error_output(output: str) -> str:
    normalized = "\n".join(line.strip() for line in output.splitlines() if line.strip())
    redacted = _LONG_ENCODED_VALUE.sub("[long encoded value omitted]", normalized)
    return _truncate_last_error(redacted)


def _truncate_last_error(error: str) -> str:
    if len(error) <= _LAST_ERROR_MAX_CHARS:
        return error
    omitted = len(error) - _LAST_ERROR_MAX_CHARS
    suffix = f"\n[earlier output truncated: {omitted} chars omitted]\n"
    keep = _LAST_ERROR_MAX_CHARS - len(suffix)
    return f"{suffix}{error[-keep:]}"


def _default_failure_message(result: ProcessRunResult) -> str:
    if result.timed_out:
        return "process timed out"
    return f"process exited with code {result.exit_code}"
