"""Session-scoped task CRUD exposed as immutable application DTOs."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    ApprovalMode,
    ErrorType,
    TaskStatus,
)
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.path_policy import validate_project_path
from ai_queue_runner.infrastructure.repositories import TaskRepository, TaskRunRepository

SessionFactory = Callable[[], Session]

# AIDEV-NOTE: v1's fixed eight-state model has no archived status/column.
# A private tag keeps completed-task archival persistent without weakening the
# state machine or requiring a schema migration; it must never leak into user tags.
_ARCHIVED_TAG = "__ai_queue_runner_archived__"


class TaskDraft(BaseModel):
    """Validated user-editable task fields; persistence fields are intentionally absent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    project_path: Path
    agent_policy: AgentPolicy = AgentPolicy.ANY
    priority: int = Field(default=50, ge=0, le=100)
    approval_mode: ApprovalMode = ApprovalMode.CONFIRM
    max_retries: int = Field(default=2, ge=0)
    retry_delay_minutes: int = Field(default=10, ge=0)
    not_before: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    dependency_ids: list[int] = Field(default_factory=list)

    @field_validator("not_before")
    @classmethod
    def _require_naive_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("not_before must be a naive UTC datetime")
        return value

    @field_validator("dependency_ids")
    @classmethod
    def _unique_dependency_ids(cls, value: list[int]) -> list[int]:
        if any(task_id <= 0 for task_id in value):
            raise ValueError("dependency IDs must be positive")
        if len(set(value)) != len(value):
            raise ValueError("dependency IDs must be unique")
        return value

    @field_validator("tags")
    @classmethod
    def _reserve_internal_tags(cls, value: list[str]) -> list[str]:
        if _ARCHIVED_TAG in value:
            raise ValueError("tag is reserved for internal use")
        return value


@dataclass(frozen=True)
class TaskView:
    id: int
    title: str
    prompt: str
    project_path: str
    agent_policy: AgentPolicy
    assigned_agent: AgentName | None
    priority: int
    status: TaskStatus
    approval_mode: ApprovalMode
    max_retries: int
    retry_count: int
    retry_delay_minutes: int
    not_before: datetime | None
    reserved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error: str | None
    tags: tuple[str, ...]
    dependency_ids: tuple[int, ...]
    archived: bool


@dataclass(frozen=True)
class RunView:
    id: int
    task_id: int
    agent: AgentName
    attempt: int
    command: tuple[str, ...]
    pid: int | None
    exit_code: int | None
    stdout_path: str | None
    stderr_path: str | None
    started_at: datetime
    finished_at: datetime | None
    result_summary: str | None
    error_type: ErrorType | None


class TaskService:
    """One-session-per-operation facade for desktop and runtime callers."""

    def __init__(self, session_factory: SessionFactory, config: AppConfig) -> None:
        self._session_factory = session_factory
        self._config = config

    def list(self, *, status: TaskStatus | str | None = None) -> list[TaskView]:
        archived_only = status == "archived"
        normalized_status = None if status is None or archived_only else TaskStatus(status)
        with self._session_factory() as session:
            tasks = TaskRepository(session).list(status=normalized_status)
            if archived_only:
                tasks = [task for task in tasks if _is_archived(task)]
            else:
                tasks = [task for task in tasks if not _is_archived(task)]
            return [_to_task_view(task) for task in tasks]

    def get(self, task_id: int) -> TaskView | None:
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            return None if task is None else _to_task_view(task)

    def create(self, draft: TaskDraft) -> TaskView:
        canonical_path = self._canonical_project_path(draft.project_path)
        with self._session_factory() as session:
            repo = TaskRepository(session)
            dependencies = self._load_dependencies(repo, draft.dependency_ids)
            task = repo.add(
                Task(
                    **_task_values(draft, canonical_path),
                    depends_on=dependencies,
                )
            )
            session.commit()
            return _to_task_view(task)

    def update(self, task_id: int, draft: TaskDraft) -> TaskView:
        canonical_path = self._canonical_project_path(draft.project_path)
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            if task.status is not TaskStatus.WAITING:
                raise ValueError("only waiting tasks can be updated")
            dependencies = self._load_dependencies(repo, draft.dependency_ids)
            if task in dependencies:
                raise ValueError("a task cannot depend on itself")
            if any(_depends_on(dependency, task_id) for dependency in dependencies):
                raise ValueError("task dependencies cannot contain a cycle")

            for field, value in _task_values(draft, canonical_path).items():
                setattr(task, field, value)
            task.depends_on = dependencies
            task.updated_at = utcnow()
            session.commit()
            return _to_task_view(task)

    def duplicate(self, task_id: int) -> TaskView:
        with self._session_factory() as session:
            source = self._require_task(TaskRepository(session), task_id)
            draft = TaskDraft(
                title=source.title,
                prompt=source.prompt,
                project_path=source.project_path,
                agent_policy=source.agent_policy,
                priority=source.priority,
                approval_mode=source.approval_mode,
                max_retries=source.max_retries,
                retry_delay_minutes=source.retry_delay_minutes,
                not_before=source.not_before,
                tags=[tag for tag in source.tags if tag != _ARCHIVED_TAG],
                dependency_ids=[task.id for task in source.depends_on],
            )
        return self.create(draft)

    def cancel(self, task_id: int) -> TaskView:
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            if task.status not in {
                TaskStatus.WAITING,
                TaskStatus.RETRY_WAIT,
                TaskStatus.BLOCKED,
            }:
                raise ValueError("only waiting, retry-wait, or blocked tasks can be cancelled")
            repo.set_status(task, TaskStatus.CANCELLED)
            task.finished_at = utcnow()
            session.commit()
            return _to_task_view(task)

    def retry(self, task_id: int) -> TaskView:
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            if task.status not in {TaskStatus.FAILED, TaskStatus.BLOCKED}:
                raise ValueError("only failed or blocked tasks can be retried")
            repo.set_status(task, TaskStatus.WAITING)
            task.assigned_agent = None
            task.retry_count = 0
            task.not_before = None
            task.reserved_at = None
            task.started_at = None
            task.finished_at = None
            task.last_error = None
            session.commit()
            return _to_task_view(task)

    def move_to_top(self, task_id: int) -> TaskView:
        """Promote a waiting task to the unique highest visible priority."""
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            if task.status is not TaskStatus.WAITING:
                raise ValueError("only waiting tasks can be moved to the top")
            updated_at = utcnow()
            for other in repo.list(status=TaskStatus.WAITING):
                if other.id != task.id and other.priority == 100:
                    other.priority = 99
                    other.updated_at = updated_at
            task.priority = 100
            task.updated_at = updated_at
            session.commit()
            return _to_task_view(task)

    def archive(self, task_id: int) -> TaskView:
        with self._session_factory() as session:
            task = self._require_task(TaskRepository(session), task_id)
            if task.status is not TaskStatus.SUCCEEDED:
                raise ValueError("only succeeded tasks can be archived")
            if not _is_archived(task):
                task.tags = [*task.tags, _ARCHIVED_TAG]
                task.updated_at = utcnow()
                session.commit()
            return _to_task_view(task)

    def list_runs(self, task_id: int) -> list[RunView]:
        with self._session_factory() as session:
            if TaskRepository(session).get(task_id) is None:
                raise KeyError(f"task {task_id} not found")
            return [
                _to_run_view(run)
                for run in TaskRunRepository(session).list_for_task(task_id)
            ]

    def _canonical_project_path(self, value: Path | str) -> Path:
        return validate_project_path(value, self._config.app.allowed_project_roots)

    @staticmethod
    def _require_task(repo: TaskRepository, task_id: int) -> Task:
        task = repo.get(task_id)
        if task is None:
            raise KeyError(f"task {task_id} not found")
        return task

    @staticmethod
    def _load_dependencies(repo: TaskRepository, dependency_ids: list[int]) -> list[Task]:
        dependencies = []
        for dependency_id in dependency_ids:
            dependency = repo.get(dependency_id)
            if dependency is None:
                raise ValueError(f"dependency task {dependency_id} not found")
            dependencies.append(dependency)
        return dependencies


def _task_values(draft: TaskDraft, canonical_path: Path) -> dict[str, object]:
    return {
        "title": draft.title,
        "prompt": draft.prompt,
        "project_path": str(canonical_path),
        "agent_policy": draft.agent_policy,
        "priority": draft.priority,
        "approval_mode": draft.approval_mode,
        "max_retries": draft.max_retries,
        "retry_delay_minutes": draft.retry_delay_minutes,
        "not_before": draft.not_before,
        "tags": list(draft.tags),
    }


def _depends_on(task: Task, target_id: int, seen: set[int] | None = None) -> bool:
    if task.id == target_id:
        return True
    seen = set() if seen is None else seen
    if task.id in seen:
        return False
    seen.add(task.id)
    return any(_depends_on(dependency, target_id, seen) for dependency in task.depends_on)


def _to_task_view(task: Task) -> TaskView:
    return TaskView(
        id=task.id,
        title=task.title,
        prompt=task.prompt,
        project_path=task.project_path,
        agent_policy=task.agent_policy,
        assigned_agent=task.assigned_agent,
        priority=task.priority,
        status=task.status,
        approval_mode=task.approval_mode,
        max_retries=task.max_retries,
        retry_count=task.retry_count,
        retry_delay_minutes=task.retry_delay_minutes,
        not_before=task.not_before,
        reserved_at=task.reserved_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_error=task.last_error,
        tags=tuple(tag for tag in task.tags if tag != _ARCHIVED_TAG),
        dependency_ids=tuple(dependency.id for dependency in task.depends_on),
        archived=_is_archived(task),
    )


def _is_archived(task: Task) -> bool:
    return _ARCHIVED_TAG in task.tags


def _to_run_view(run: TaskRun) -> RunView:
    return RunView(
        id=run.id,
        task_id=run.task_id,
        agent=run.agent,
        attempt=run.attempt,
        command=tuple(run.command),
        pid=run.pid,
        exit_code=run.exit_code,
        stdout_path=run.stdout_path,
        stderr_path=run.stderr_path,
        started_at=run.started_at,
        finished_at=run.finished_at,
        result_summary=run.result_summary,
        error_type=run.error_type,
    )
