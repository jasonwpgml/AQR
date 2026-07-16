"""Recovery routines that return stuck work to the queue."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import TaskStatus
from ai_queue_runner.domain.models import AgentState, Task
from ai_queue_runner.infrastructure.repositories import TaskRepository


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
