"""Session-scoped task CRUD exposed as immutable application DTOs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import delete as sql_delete
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
    estimated_tokens: int = Field(default=0, ge=0)
    approval_mode: ApprovalMode = ApprovalMode.CONFIRM
    max_retries: int = Field(default=2, ge=0)
    retry_delay_minutes: int = Field(default=10, ge=0)
    recurrence_interval_days: int | None = Field(default=None, ge=1)
    not_before: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    dependency_ids: list[int] = Field(default_factory=list)
    depends_on_indices: list[int] = Field(default_factory=list)

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

    @field_validator("depends_on_indices")
    @classmethod
    def _unique_dependency_indices(cls, value: list[int]) -> list[int]:
        if any(index < 0 for index in value):
            raise ValueError("sibling dependency indices must be non-negative")
        if len(set(value)) != len(value):
            raise ValueError("sibling dependency indices must be unique")
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
    fallback_agent: AgentName | None
    priority: int
    status: TaskStatus
    approval_mode: ApprovalMode
    max_retries: int
    retry_count: int
    retry_delay_minutes: int
    recurrence_interval_days: int | None
    recurrence_source_task_id: int | None
    not_before: datetime | None
    reserved_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime
    last_error: str | None
    tags: tuple[str, ...]
    dependency_ids: tuple[int, ...]
    split_parent_task_id: int | None
    split_child_ids: tuple[int, ...]
    archived: bool
    use_worktree: bool
    worktree_path: str | None
    worktree_branch: str | None
    estimated_tokens: int = 0


@dataclass(frozen=True)
class TaskSplitView:
    original: TaskView
    children: tuple[TaskView, ...]
    rewired_dependent_ids: tuple[int, ...]


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
    fallback_to_agent: AgentName | None
    worktree_path: str | None
    worktree_branch: str | None


@dataclass(frozen=True)
class CalendarActivityView:
    id: int
    title: str
    status: TaskStatus
    created_at: datetime
    finished_at: datetime | None
    not_before: datetime | None


@dataclass(frozen=True)
class FailedTaskDailySummaryView:
    id: int
    title: str
    last_error_type: ErrorType | None


@dataclass(frozen=True)
class ProjectDailySummaryView:
    project_path: str
    succeeded_count: int
    failed_count: int
    blocked_count: int
    cancelled_count: int
    total_runtime_seconds: float
    claude_run_count: int
    codex_run_count: int
    fallback_count: int
    worktree_branches: tuple[str, ...]
    failed_tasks: tuple[FailedTaskDailySummaryView, ...]


@dataclass(frozen=True)
class DailySummaryTotalsView:
    project_count: int
    succeeded_count: int
    failed_count: int
    blocked_count: int
    cancelled_count: int
    total_runtime_seconds: float
    claude_run_count: int
    codex_run_count: int
    fallback_count: int
    worktree_branches: tuple[str, ...]
    failed_task_count: int
    claude_usage_limit_wait_occurred: bool
    codex_usage_limit_wait_occurred: bool


@dataclass(frozen=True)
class DailySummaryView:
    local_date: date
    start_utc: datetime
    end_utc: datetime
    projects: tuple[ProjectDailySummaryView, ...]
    totals: DailySummaryTotalsView


@dataclass
class _ProjectDailyAccumulator:
    status_counts: Counter[TaskStatus] = field(default_factory=Counter)
    agent_run_counts: Counter[AgentName] = field(default_factory=Counter)
    total_runtime_seconds: float = 0.0
    fallback_count: int = 0
    worktree_branches: set[str] = field(default_factory=set)
    failed_tasks: list[FailedTaskDailySummaryView] = field(default_factory=list)


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

    def activity_between(
        self,
        start_utc: datetime,
        end_utc: datetime,
    ) -> list[CalendarActivityView]:
        if start_utc.tzinfo is not None or end_utc.tzinfo is not None:
            raise ValueError("calendar boundaries must be naive UTC datetimes")
        if start_utc >= end_utc:
            raise ValueError("calendar start must be before end")
        with self._session_factory() as session:
            tasks = TaskRepository(session).list()
            return [
                _to_calendar_activity_view(task)
                for task in tasks
                if any(
                    value is not None and start_utc <= value < end_utc
                    for value in (task.created_at, task.finished_at, task.not_before)
                )
            ]

    def daily_summary(self, local_date: date | None = None) -> DailySummaryView:
        selected_date = local_date or _local_today()
        start_utc, end_utc = _local_date_utc_range(selected_date)
        with self._session_factory() as session:
            task_repo = TaskRepository(session)
            run_repo = TaskRunRepository(session)
            terminal_tasks = task_repo.list_terminal_activity_between(start_utc, end_utc)
            finished_runs = run_repo.list_finished_between(start_utc, end_utc)

            projects: dict[str, _ProjectDailyAccumulator] = {}

            def project_for(path: str) -> _ProjectDailyAccumulator:
                return projects.setdefault(path, _ProjectDailyAccumulator())

            for task in terminal_tasks:
                project = project_for(task.project_path)
                project.status_counts[task.status] += 1
                if task.worktree_branch:
                    project.worktree_branches.add(task.worktree_branch)
                if task.status is TaskStatus.FAILED:
                    latest_run = max(
                        task.runs,
                        key=lambda run: (run.finished_at or run.started_at, run.id),
                        default=None,
                    )
                    project.failed_tasks.append(
                        FailedTaskDailySummaryView(
                            id=task.id,
                            title=task.title,
                            last_error_type=(
                                None if latest_run is None else latest_run.error_type
                            ),
                        )
                    )

            usage_limit_wait = {agent: False for agent in AgentName}
            for run in finished_runs:
                project = project_for(run.task.project_path)
                project.agent_run_counts[run.agent] += 1
                project.total_runtime_seconds += max(
                    0.0,
                    (run.finished_at - run.started_at).total_seconds(),
                )
                if run.fallback_to_agent is not None:
                    project.fallback_count += 1
                if run.worktree_branch:
                    project.worktree_branches.add(run.worktree_branch)
                if run.error_type is ErrorType.USAGE_LIMIT:
                    usage_limit_wait[run.agent] = True

            project_views = tuple(
                _to_project_daily_summary(path, projects[path])
                for path in sorted(projects, key=str.casefold)
            )
            all_branches = tuple(
                sorted(
                    {
                        branch
                        for project in project_views
                        for branch in project.worktree_branches
                    }
                )
            )
            totals = DailySummaryTotalsView(
                project_count=len(project_views),
                succeeded_count=sum(project.succeeded_count for project in project_views),
                failed_count=sum(project.failed_count for project in project_views),
                blocked_count=sum(project.blocked_count for project in project_views),
                cancelled_count=sum(project.cancelled_count for project in project_views),
                total_runtime_seconds=sum(
                    project.total_runtime_seconds for project in project_views
                ),
                claude_run_count=sum(
                    project.claude_run_count for project in project_views
                ),
                codex_run_count=sum(project.codex_run_count for project in project_views),
                fallback_count=sum(project.fallback_count for project in project_views),
                worktree_branches=all_branches,
                failed_task_count=sum(
                    len(project.failed_tasks) for project in project_views
                ),
                claude_usage_limit_wait_occurred=usage_limit_wait[AgentName.CLAUDE],
                codex_usage_limit_wait_occurred=usage_limit_wait[AgentName.CODEX],
            )
            return DailySummaryView(
                local_date=selected_date,
                start_utc=start_utc,
                end_utc=end_utc,
                projects=project_views,
                totals=totals,
            )

    def create(self, draft: TaskDraft) -> TaskView:
        return self.create_batch([draft])[0]

    def validate_batch(self, drafts: list[TaskDraft]) -> None:
        """Validate a complete batch without changing the queue."""
        if not drafts:
            return
        validate_sibling_dependency_indices(drafts)
        for draft in drafts:
            self._canonical_project_path(draft.project_path)
        with self._session_factory() as session:
            repo = TaskRepository(session)
            self._load_batch_dependencies(repo, drafts)

    def create_batch(self, drafts: list[TaskDraft]) -> list[TaskView]:
        """Create every draft in one transaction or leave the queue unchanged."""
        if not drafts:
            return []
        validate_sibling_dependency_indices(drafts)
        canonical_paths = [
            self._canonical_project_path(draft.project_path) for draft in drafts
        ]
        with self._session_factory() as session:
            repo = TaskRepository(session)
            dependencies = self._load_batch_dependencies(repo, drafts)
            tasks = self._add_batch(repo, drafts, canonical_paths, dependencies)
            session.commit()
            return [_to_task_view(task) for task in tasks]

    def split_task(
        self,
        task_id: int,
        drafts: list[TaskDraft],
        *,
        dry_run: bool = False,
    ) -> TaskSplitView:
        """Replace one queued task with a dependent child batch in one transaction."""
        if not drafts:
            raise ValueError("a split requires at least one child task")
        validate_sibling_dependency_indices(drafts)
        canonical_paths = [
            self._canonical_project_path(draft.project_path) for draft in drafts
        ]
        with self._session_factory() as session:
            repo = TaskRepository(session)
            original = self._require_task(repo, task_id)
            if original.status not in {
                TaskStatus.WAITING,
                TaskStatus.RETRY_WAIT,
                TaskStatus.BLOCKED,
            }:
                raise ValueError(
                    "only waiting, retry-wait, or blocked tasks can be split"
                )
            dependencies = self._load_batch_dependencies(repo, drafts)
            for child_dependencies in dependencies:
                if any(
                    dependency.id == original.id
                    or _depends_on(dependency, original.id)
                    for dependency in child_dependencies
                ):
                    raise ValueError(
                        "split child dependencies cannot create a cycle through "
                        f"original task {original.id}"
                    )
            if dry_run:
                dependent_ids = tuple(
                    task.id for task in sorted(original.dependents, key=lambda task: task.id)
                )
                return TaskSplitView(
                    original=_to_task_view(original),
                    children=(),
                    rewired_dependent_ids=dependent_ids,
                )

            # AIDEV-NOTE: claim cancellation before inserting children so a scheduler
            # reservation cannot race the split; every later write shares this transaction.
            repo.set_status(original, TaskStatus.CANCELLED, compare_and_swap=True)
            dependents = sorted(original.dependents, key=lambda task: task.id)
            dependent_ids = tuple(task.id for task in dependents)
            original.finished_at = utcnow()
            children = self._add_batch(
                repo,
                drafts,
                canonical_paths,
                dependencies,
                split_parent_task_id=original.id,
            )
            for dependent in dependents:
                dependent.depends_on.remove(original)
                for child in children:
                    repo.add_dependency(dependent, child)
            session.commit()
            return TaskSplitView(
                original=_to_task_view(original),
                children=tuple(_to_task_view(child) for child in children),
                rewired_dependent_ids=dependent_ids,
            )

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

            project_changed = task.project_path != str(canonical_path)
            if (
                project_changed
                and task.worktree_path
                and Path(task.worktree_path).exists()
            ):
                raise ValueError("clean up the task worktree before changing its project")
            for field, value in _task_values(draft, canonical_path).items():
                setattr(task, field, value)
            if project_changed:
                task.use_worktree = False
                task.worktree_path = None
                task.worktree_branch = None
            if draft.agent_policy is not AgentPolicy.ANY:
                task.fallback_agent = None
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
                estimated_tokens=source.estimated_tokens,
                approval_mode=source.approval_mode,
                max_retries=source.max_retries,
                retry_delay_minutes=source.retry_delay_minutes,
                recurrence_interval_days=source.recurrence_interval_days,
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
            task.fallback_agent = None
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

    def delete(self, task_id: int) -> None:
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            if task.status in {TaskStatus.RESERVED, TaskStatus.RUNNING}:
                raise ValueError("reserved or running tasks must be cancelled before deletion")
            if task.worktree_path and Path(task.worktree_path).exists():
                raise ValueError("clean up the task worktree before deleting its history")
            dependent_ids = sorted(dependent.id for dependent in task.dependents)
            if dependent_ids:
                joined_ids = ", ".join(str(dependent_id) for dependent_id in dependent_ids)
                raise ValueError(f"task {task_id} is required by dependent tasks: {joined_ids}")
            split_child_ids = [child.id for child in task.split_children]
            if split_child_ids:
                joined_ids = ", ".join(str(child_id) for child_id in split_child_ids)
                raise ValueError(
                    f"task {task_id} is the split parent of child tasks: {joined_ids}"
                )
            session.execute(sql_delete(TaskRun).where(TaskRun.task_id == task_id))
            repo.delete(task)
            session.commit()

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

    @classmethod
    def _load_batch_dependencies(
        cls,
        repo: TaskRepository,
        drafts: list[TaskDraft],
    ) -> list[list[Task]]:
        return [cls._load_dependencies(repo, draft.dependency_ids) for draft in drafts]

    @staticmethod
    def _add_batch(
        repo: TaskRepository,
        drafts: list[TaskDraft],
        canonical_paths: list[Path],
        dependencies: list[list[Task]],
        *,
        split_parent_task_id: int | None = None,
    ) -> list[Task]:
        tasks = [
            repo.add(
                Task(
                    **_task_values(draft, canonical_path),
                    depends_on=child_dependencies,
                    split_parent_task_id=split_parent_task_id,
                )
            )
            for draft, canonical_path, child_dependencies in zip(
                drafts,
                canonical_paths,
                dependencies,
                strict=True,
            )
        ]
        for task, draft in zip(tasks, drafts, strict=True):
            for dependency_index in draft.depends_on_indices:
                repo.add_dependency(task, tasks[dependency_index])
        return tasks


def validate_sibling_dependency_indices(drafts: list[TaskDraft]) -> None:
    """Reject invalid sibling references and cycles before any task is inserted."""
    task_count = len(drafts)
    for task_index, draft in enumerate(drafts):
        for dependency_index in draft.depends_on_indices:
            if dependency_index >= task_count:
                raise ValueError(
                    f"depends_on_indices index {dependency_index} is out of range "
                    f"for {task_count} tasks"
                )
            if dependency_index == task_index:
                raise ValueError(f"task index {task_index} cannot depend on itself")

    visiting: set[int] = set()
    visited: set[int] = set()

    def visit(task_index: int) -> None:
        if task_index in visiting:
            raise ValueError("depends_on_indices cannot contain a cycle")
        if task_index in visited:
            return
        visiting.add(task_index)
        for dependency_index in drafts[task_index].depends_on_indices:
            visit(dependency_index)
        visiting.remove(task_index)
        visited.add(task_index)

    for task_index in range(task_count):
        visit(task_index)


def _task_values(draft: TaskDraft, canonical_path: Path) -> dict[str, object]:
    return {
        "title": draft.title,
        "prompt": draft.prompt,
        "project_path": str(canonical_path),
        "agent_policy": draft.agent_policy,
        "priority": draft.priority,
        "estimated_tokens": draft.estimated_tokens,
        "approval_mode": draft.approval_mode,
        "max_retries": draft.max_retries,
        "retry_delay_minutes": draft.retry_delay_minutes,
        "recurrence_interval_days": draft.recurrence_interval_days,
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
        fallback_agent=task.fallback_agent,
        priority=task.priority,
        status=task.status,
        approval_mode=task.approval_mode,
        max_retries=task.max_retries,
        retry_count=task.retry_count,
        retry_delay_minutes=task.retry_delay_minutes,
        recurrence_interval_days=task.recurrence_interval_days,
        recurrence_source_task_id=task.recurrence_source_task_id,
        not_before=task.not_before,
        reserved_at=task.reserved_at,
        started_at=task.started_at,
        finished_at=task.finished_at,
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_error=task.last_error,
        tags=tuple(tag for tag in task.tags if tag != _ARCHIVED_TAG),
        dependency_ids=tuple(sorted(dependency.id for dependency in task.depends_on)),
        split_parent_task_id=task.split_parent_task_id,
        split_child_ids=tuple(child.id for child in task.split_children),
        archived=_is_archived(task),
        use_worktree=task.use_worktree,
        worktree_path=task.worktree_path,
        worktree_branch=task.worktree_branch,
        estimated_tokens=task.estimated_tokens,
    )


def _to_calendar_activity_view(task: Task) -> CalendarActivityView:
    return CalendarActivityView(
        id=task.id,
        title=task.title,
        status=task.status,
        created_at=task.created_at,
        finished_at=task.finished_at,
        not_before=task.not_before,
    )


def _to_project_daily_summary(
    project_path: str,
    value: _ProjectDailyAccumulator,
) -> ProjectDailySummaryView:
    return ProjectDailySummaryView(
        project_path=project_path,
        succeeded_count=value.status_counts[TaskStatus.SUCCEEDED],
        failed_count=value.status_counts[TaskStatus.FAILED],
        blocked_count=value.status_counts[TaskStatus.BLOCKED],
        cancelled_count=value.status_counts[TaskStatus.CANCELLED],
        total_runtime_seconds=value.total_runtime_seconds,
        claude_run_count=value.agent_run_counts[AgentName.CLAUDE],
        codex_run_count=value.agent_run_counts[AgentName.CODEX],
        fallback_count=value.fallback_count,
        worktree_branches=tuple(sorted(value.worktree_branches)),
        failed_tasks=tuple(sorted(value.failed_tasks, key=lambda task: task.id)),
    )


def _local_today() -> date:
    return utcnow().replace(tzinfo=UTC).astimezone().date()


def _local_date_utc_range(value: date) -> tuple[datetime, datetime]:
    try:
        next_date = value + timedelta(days=1)
    except OverflowError as exc:
        raise ValueError("daily summary date is out of range") from exc
    start_local = datetime.combine(value, time.min).astimezone()
    end_local = datetime.combine(next_date, time.min).astimezone()
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
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
        fallback_to_agent=run.fallback_to_agent,
        worktree_path=run.worktree_path,
        worktree_branch=run.worktree_branch,
    )
