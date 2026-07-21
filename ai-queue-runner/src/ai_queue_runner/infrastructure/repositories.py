"""Thin repository layer over the ORM models.

Repositories mutate objects inside the caller's Session; the caller owns
commit/rollback. This keeps multi-step operations (e.g. reserve task + update
agent state) in one transaction — required by plan §5.4.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import case, exists, select, update
from sqlalchemy.orm import Session, joinedload, selectinload

from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, TaskCreator, TaskStatus
from ai_queue_runner.domain.models import (
    AgentState,
    Setting,
    Task,
    TaskEvent,
    TaskReview,
    TaskRun,
    TaskTemplate,
)
from ai_queue_runner.domain.transitions import validate_transition


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task: Task) -> Task:
        self._session.add(task)
        self._session.flush()
        return task

    def get(self, task_id: int) -> Task | None:
        return self._session.get(Task, task_id)

    def get_next_occurrence(self, source_task_id: int) -> Task | None:
        return self._session.scalar(
            select(Task).where(Task.recurrence_source_task_id == source_task_id)
        )

    def list(
        self,
        *,
        status: TaskStatus | None = None,
        project_path: str | None = None,
        created_by: TaskCreator | None = None,
        creator_session_id: str | None = None,
    ) -> list[Task]:
        stmt = select(Task).options(selectinload(Task.reviews)).order_by(Task.id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        if project_path is not None:
            stmt = stmt.where(Task.project_path == project_path)
        if created_by is not None:
            stmt = stmt.where(Task.created_by == created_by)
        if creator_session_id is not None:
            stmt = stmt.where(Task.creator_session_id == creator_session_id)
        return list(self._session.scalars(stmt))

    def list_pending_reviews(self, *, project_path: str | None = None) -> list[Task]:
        stmt = (
            select(Task)
            .where(
                Task.status == TaskStatus.SUCCEEDED,
                ~exists().where(TaskReview.task_id == Task.id),
            )
            .order_by(Task.finished_at, Task.id)
        )
        if project_path is not None:
            stmt = stmt.where(Task.project_path == project_path)
        return list(self._session.scalars(stmt))

    def list_terminal_activity_between(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[Task]:
        activity_at = case(
            (Task.status == TaskStatus.BLOCKED, Task.updated_at),
            else_=Task.finished_at,
        )
        stmt = (
            select(Task)
            .options(selectinload(Task.runs))
            .where(
                Task.status.in_(
                    (
                        TaskStatus.SUCCEEDED,
                        TaskStatus.FAILED,
                        TaskStatus.BLOCKED,
                        TaskStatus.CANCELLED,
                    )
                ),
                activity_at >= start_utc,
                activity_at < end_utc,
            )
            .order_by(Task.project_path, Task.id)
        )
        return list(self._session.scalars(stmt))

    def delete(self, task: Task) -> None:
        self._session.delete(task)

    def set_status(
        self,
        task: Task,
        new_status: TaskStatus,
        *,
        compare_and_swap: bool = False,
    ) -> None:
        """The only sanctioned way to change a task's status."""
        current_status = task.status
        validate_transition(current_status, new_status)
        updated_at = utcnow()
        if not compare_and_swap:
            task.status = new_status
            task.updated_at = updated_at
            return
        changed = self._session.execute(
            update(Task)
            .where(Task.id == task.id, Task.status == current_status)
            .values(status=new_status, updated_at=updated_at)
            .execution_options(synchronize_session=False)
        ).rowcount
        if changed != 1:
            raise ValueError(
                f"task {task.id} status changed while the operation was in progress"
            )
        self._session.refresh(task)

    def add_dependency(self, task: Task, depends_on: Task) -> None:
        if depends_on is task:
            raise ValueError("a task cannot depend on itself")
        task.depends_on.append(depends_on)


class AgentStateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, agent: AgentName) -> AgentState | None:
        return self._session.get(AgentState, agent)

    def get_or_create(self, agent: AgentName) -> AgentState:
        state = self.get(agent)
        if state is None:
            state = AgentState(agent=agent)
            self._session.add(state)
            self._session.flush()
        return state


class TaskRunRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: TaskRun) -> TaskRun:
        self._session.add(run)
        self._session.flush()
        return run

    def list_for_task(self, task_id: int) -> list[TaskRun]:
        stmt = (
            select(TaskRun)
            .where(TaskRun.task_id == task_id)
            .order_by(TaskRun.attempt, TaskRun.id)
        )
        return list(self._session.scalars(stmt))

    def list_finished_between(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[TaskRun]:
        stmt = (
            select(TaskRun)
            .options(joinedload(TaskRun.task))
            .where(
                TaskRun.finished_at >= start_utc,
                TaskRun.finished_at < end_utc,
            )
            .order_by(TaskRun.finished_at, TaskRun.id)
        )
        return list(self._session.scalars(stmt))


class TaskReviewRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, review: TaskReview) -> TaskReview:
        self._session.add(review)
        self._session.flush()
        return review

    def list_for_task(self, task_id: int) -> list[TaskReview]:
        stmt = (
            select(TaskReview)
            .where(TaskReview.task_id == task_id)
            .order_by(TaskReview.id)
        )
        return list(self._session.scalars(stmt))


class TaskEventRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, task_event: TaskEvent) -> TaskEvent:
        self._session.add(task_event)
        self._session.flush()
        return task_event

    def list(
        self,
        *,
        task_id: int | None = None,
        task_created_at: datetime | None = None,
        project_path: str | None = None,
        actor: TaskCreator | None = None,
        limit: int | None = None,
    ) -> list[TaskEvent]:
        stmt = select(TaskEvent)
        if task_id is not None:
            stmt = stmt.where(TaskEvent.task_id == task_id)
        if task_created_at is not None:
            stmt = stmt.where(TaskEvent.task_created_at == task_created_at)
        if project_path is not None:
            stmt = stmt.where(TaskEvent.project_path == project_path)
        if actor is not None:
            stmt = stmt.where(TaskEvent.actor == actor)
        stmt = stmt.order_by(TaskEvent.created_at.desc(), TaskEvent.id.desc())
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self._session.scalars(stmt))


class TaskTemplateRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, template: TaskTemplate) -> TaskTemplate:
        self._session.add(template)
        self._session.flush()
        return template

    def get(self, name: str) -> TaskTemplate | None:
        return self._session.get(TaskTemplate, name)

    def list(self) -> list[TaskTemplate]:
        return list(
            self._session.scalars(select(TaskTemplate).order_by(TaskTemplate.name))
        )

    def delete(self, template: TaskTemplate) -> None:
        self._session.delete(template)


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, key: str, default: object = None) -> object:
        row = self._session.get(Setting, key)
        return default if row is None else row.value

    def set(self, key: str, value: object) -> None:
        row = self._session.get(Setting, key)
        if row is None:
            self._session.add(Setting(key=key, value=value))
        else:
            row.value = value
