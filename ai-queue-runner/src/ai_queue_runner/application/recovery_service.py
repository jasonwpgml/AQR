"""Recovery routines that return stuck work to the queue."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

import psutil
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import AgentState, Task, TaskRun
from ai_queue_runner.infrastructure.process_runner import (
    ProcessIdentityError,
    get_process_started_at,
    kill_process_tree,
)
from ai_queue_runner.infrastructure.repositories import TaskRepository


@dataclass(frozen=True)
class OrphanedRun:
    task_id: int
    run_id: int | None
    pid: int | None
    pid_alive: bool
    pid_identity_verified: bool = False


class _ProcessStatus(Enum):
    NOT_RUNNING = "not_running"
    MATCH = "match"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


def find_orphaned_running_tasks(session: Session) -> list[OrphanedRun]:
    """Find tasks left running by a previous app session.

    The caller must invoke this only at startup, before any executor is active. Every
    ``running`` task is therefore orphaned; this function does not try to infer whether
    the current app owns an executor. The query is read-only and does not commit or mutate.
    """
    running_tasks = session.scalars(
        select(Task).where(Task.status == TaskStatus.RUNNING).order_by(Task.id)
    ).all()
    orphaned = []
    for task in running_tasks:
        run = _latest_run(session, task.id)
        pid = None if run is None else run.pid
        process_status = _run_process_status(run)
        orphaned.append(
            OrphanedRun(
                task_id=task.id,
                run_id=None if run is None else run.id,
                pid=pid,
                pid_alive=process_status in {_ProcessStatus.MATCH, _ProcessStatus.UNKNOWN},
                pid_identity_verified=process_status is _ProcessStatus.MATCH,
            )
        )
    return orphaned


def recover_orphaned_task(
    session: Session,
    task: Task,
    *,
    to_status: TaskStatus,
    kill_if_alive: bool = False,
) -> None:
    """Recover one orphaned running task to ``failed`` or ``waiting``; owns the commit."""
    if to_status is not TaskStatus.FAILED and to_status is not TaskStatus.WAITING:
        raise ValueError("orphaned tasks can only be recovered to failed or waiting")

    run = _latest_run(session, task.id)
    pid = None if run is None else run.pid
    process_status = _run_process_status(run)
    if process_status is _ProcessStatus.UNKNOWN:
        raise RuntimeError(f"task {task.id} process identity could not be verified")
    if process_status is _ProcessStatus.MATCH:
        if not kill_if_alive:
            raise RuntimeError(f"task {task.id} process {pid} is still alive")
        try:
            kill_process_tree(pid, expected_started_at=run.started_at)
        except ProcessIdentityError as exc:
            raise RuntimeError(f"task {task.id} process identity changed") from exc

    finished_at = utcnow()
    if run is not None:
        run.finished_at = finished_at
        run.error_type = ErrorType.CRASH

    TaskRepository(session).set_status(task, to_status)
    task.last_error = "recovered after crash"
    if to_status is TaskStatus.FAILED:
        task.finished_at = finished_at
    else:
        task.assigned_agent = None
        task.reserved_at = None
        task.started_at = None

    session.execute(
        update(AgentState)
        .where(AgentState.current_task_id == task.id)
        .values(current_task_id=None, status=AgentStatus.UNKNOWN)
    )
    session.commit()


def _latest_run(session: Session, task_id: int) -> TaskRun | None:
    return session.scalar(
        select(TaskRun)
        .where(TaskRun.task_id == task_id)
        .order_by(TaskRun.attempt.desc(), TaskRun.id.desc())
        .limit(1)
    )


def _run_process_status(run: TaskRun | None) -> _ProcessStatus:
    if run is None or run.pid is None or run.started_at is None:
        return _ProcessStatus.NOT_RUNNING
    # AIDEV-NOTE: PID existence alone is unsafe because Windows can reuse a PID.
    # Exact OS creation time is persisted at spawn and forms the second half of
    # the process identity; a mismatch must never be killed during recovery.
    if not psutil.pid_exists(run.pid):
        return _ProcessStatus.NOT_RUNNING
    actual_started_at = get_process_started_at(run.pid)
    if actual_started_at is None:
        return _ProcessStatus.UNKNOWN
    if actual_started_at == run.started_at:
        return _ProcessStatus.MATCH
    return _ProcessStatus.MISMATCH


def release_stale_reservations(
    session: Session,
    config: AppConfig,
    now: datetime | None = None,
) -> list[Task]:
    """Return tasks stuck in ``reserved`` past the timeout back to ``waiting``.

    Owns the commit. Also clears agent_states.current_task_id pointing at the
    released tasks so the agent becomes reservable again.

    AIDEV-NOTE: safe only because execution moves reserved -> running *before*
    spawning the CLI process; a task that actually started is `running` and is
    recovered by the PID-based crash recovery (plan §8.5), never by this.
    """
    now = utcnow() if now is None else now
    cutoff = now - timedelta(minutes=config.scheduler.reserved_timeout_minutes)

    repo = TaskRepository(session)
    stale = list(
        session.scalars(
            select(Task).where(Task.status == TaskStatus.RESERVED, Task.reserved_at <= cutoff)
        )
    )
    if not stale:
        session.rollback()
        return []

    for task in stale:
        repo.set_status(task, TaskStatus.WAITING)
        task.assigned_agent = None
        task.reserved_at = None

    session.execute(
        update(AgentState)
        .where(AgentState.current_task_id.in_([task.id for task in stale]))
        .values(current_task_id=None)
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return stale


def promote_retry_tasks(
    session: Session,
    now: datetime | None = None,
) -> list[Task]:
    """Promote due retry tasks back to the schedulable waiting state."""
    now = utcnow() if now is None else now
    retry_tasks = list(
        session.scalars(
            select(Task).where(
                Task.status == TaskStatus.RETRY_WAIT,
                (Task.not_before.is_(None) | (Task.not_before <= now)),
            )
        )
    )
    if not retry_tasks:
        session.rollback()
        return []

    repo = TaskRepository(session)
    for task in retry_tasks:
        repo.set_status(task, TaskStatus.WAITING)
    session.commit()
    return retry_tasks
