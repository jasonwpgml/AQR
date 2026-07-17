"""Thin repository layer over the ORM models.

Repositories mutate objects inside the caller's Session; the caller owns
commit/rollback. This keeps multi-step operations (e.g. reserve task + update
agent state) in one transaction — required by plan §5.4.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, TaskStatus
from ai_queue_runner.domain.models import AgentState, Setting, Task, TaskRun
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

    def list(self, *, status: TaskStatus | None = None) -> list[Task]:
        stmt = select(Task).order_by(Task.id)
        if status is not None:
            stmt = stmt.where(Task.status == status)
        return list(self._session.scalars(stmt))

    def delete(self, task: Task) -> None:
        self._session.delete(task)

    def set_status(self, task: Task, new_status: TaskStatus) -> None:
        """The only sanctioned way to change a task's status."""
        validate_transition(task.status, new_status)
        task.status = new_status
        task.updated_at = utcnow()

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
