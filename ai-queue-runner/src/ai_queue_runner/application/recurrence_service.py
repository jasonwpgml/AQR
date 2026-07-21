"""Create the next occurrence of a successfully completed recurring task."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ai_queue_runner.domain.enums import TaskCreator, TaskEventAction, TaskStatus
from ai_queue_runner.domain.models import Task, TaskEvent
from ai_queue_runner.infrastructure.repositories import TaskEventRepository, TaskRepository


def enqueue_next_occurrence(
    session: Session,
    completed_task: Task,
    *,
    completed_at: datetime,
) -> Task | None:
    """Ensure one waiting successor exists for a recurring successful task."""
    if completed_task.status is not TaskStatus.SUCCEEDED:
        raise ValueError("only succeeded tasks can enqueue a recurring occurrence")
    if completed_task.recurrence_interval_days is None:
        return None
    if completed_task.id is None:
        raise ValueError("a recurring source task must be persisted")
    if completed_at.tzinfo is not None:
        raise ValueError("completed_at must be a naive UTC datetime")

    repo = TaskRepository(session)
    existing = repo.get_next_occurrence(completed_task.id)
    if existing is not None:
        return existing

    successor = Task(
        title=completed_task.title,
        prompt=completed_task.prompt,
        project_path=completed_task.project_path,
        created_by=completed_task.created_by,
        creator_session_id=completed_task.creator_session_id,
        agent_policy=completed_task.agent_policy,
        priority=completed_task.priority,
        estimated_tokens=completed_task.estimated_tokens,
        approval_mode=completed_task.approval_mode,
        auto_commit=completed_task.auto_commit,
        draft_pr=completed_task.draft_pr,
        max_retries=completed_task.max_retries,
        retry_delay_minutes=completed_task.retry_delay_minutes,
        recurrence_interval_days=completed_task.recurrence_interval_days,
        recurrence_source_task_id=completed_task.id,
        not_before=completed_at
        + timedelta(days=completed_task.recurrence_interval_days),
        tags=list(completed_task.tags),
        depends_on=list(completed_task.depends_on),
    )
    successor = repo.add(successor)
    TaskEventRepository(session).add(
        TaskEvent(
            task_id=successor.id,
            task_created_at=successor.created_at,
            action=TaskEventAction.CREATED,
            actor=TaskCreator.SYSTEM,
            project_path=successor.project_path,
            task_title=successor.title,
            details={"recurrence_source_task_id": completed_task.id},
        )
    )
    return successor
