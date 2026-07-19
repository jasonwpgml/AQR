"""Session-scoped task CRUD exposed as immutable application DTOs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete as sql_delete
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    ApprovalMode,
    ErrorType,
    ReviewVerdict,
    TaskCreator,
    TaskEventAction,
    TaskStatus,
)
from ai_queue_runner.domain.models import Task, TaskEvent, TaskReview, TaskRun
from ai_queue_runner.infrastructure.path_policy import validate_project_path
from ai_queue_runner.infrastructure.repositories import (
    TaskEventRepository,
    TaskRepository,
    TaskReviewRepository,
    TaskRunRepository,
)

SessionFactory = Callable[[], Session]

# AIDEV-NOTE: v1's fixed eight-state model has no archived status/column.
# A private tag keeps completed-task archival persistent without weakening the
# state machine or requiring a schema migration; it must never leak into user tags.
_ARCHIVED_TAG = "__ai_queue_runner_archived__"
_ACTIVE_STATUSES = {
    TaskStatus.WAITING,
    TaskStatus.RESERVED,
    TaskStatus.RUNNING,
    TaskStatus.RETRY_WAIT,
    TaskStatus.BLOCKED,
}


class TaskDraft(BaseModel):
    """Validated user-editable task fields; persistence fields are intentionally absent."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    project_path: Path
    created_by: TaskCreator | None = None
    creator_session_id: str | None = Field(default=None, max_length=256)
    agent_policy: AgentPolicy = AgentPolicy.ANY
    priority: int = Field(default=50, ge=0, le=100)
    estimated_tokens: int = Field(default=0, ge=0)
    approval_mode: ApprovalMode = ApprovalMode.CONFIRM
    auto_commit: bool = False
    draft_pr: bool = False
    max_retries: int = Field(default=2, ge=0)
    retry_delay_minutes: int = Field(default=10, ge=0)
    recurrence_interval_days: int | None = Field(default=None, ge=1)
    not_before: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    dependency_ids: list[int] = Field(default_factory=list)
    depends_on_indices: list[int] = Field(default_factory=list)

    @model_validator(mode="after")
    def _require_auto_commit_for_draft_pr(self) -> TaskDraft:
        if self.draft_pr and not self.auto_commit:
            raise ValueError("draft_pr requires auto_commit")
        return self

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
    created_by: TaskCreator
    creator_session_id: str | None
    agent_policy: AgentPolicy
    assigned_agent: AgentName | None
    fallback_agent: AgentName | None
    priority: int
    status: TaskStatus
    approval_mode: ApprovalMode
    auto_commit: bool
    draft_pr: bool
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
    rework_of_task_id: int | None
    rework_task_ids: tuple[int, ...]
    review_verdict: ReviewVerdict | None
    review_score: int | None
    review_notes: str | None
    reviewed_at: datetime | None
    review_count: int
    archived: bool
    use_worktree: bool
    worktree_path: str | None
    worktree_branch: str | None
    estimated_tokens: int = 0


@dataclass(frozen=True)
class PendingReviewView:
    id: int
    title: str
    project_path: str
    finished_at: datetime | None


@dataclass(frozen=True)
class ReviewView:
    id: int
    task_id: int
    verdict: ReviewVerdict
    score: int | None
    notes: str | None
    reviewed_at: datetime
    revision: int


@dataclass(frozen=True)
class TaskReviewResultView:
    task: TaskView
    review: ReviewView
    previous_review_count: int
    rework_task: TaskView | None


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
    auto_commit_hash: str | None
    auto_commit_result: str | None
    draft_pr_url: str | None
    draft_pr_result: str | None


@dataclass(frozen=True)
class TaskEventView:
    id: int
    task_id: int
    task_created_at: datetime
    action: TaskEventAction
    actor: TaskCreator
    actor_session_id: str | None
    project_path: str
    task_title: str
    details: dict[str, object]
    created_at: datetime


@dataclass(frozen=True)
class BulkDeleteSkippedView:
    id: int
    title: str
    reason: str


@dataclass(frozen=True)
class BulkDeleteResultView:
    project_path: str
    matched_count: int
    deleted_ids: tuple[int, ...]
    skipped: tuple[BulkDeleteSkippedView, ...]


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
    gemini_run_count: int
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
    gemini_run_count: int
    fallback_count: int
    worktree_branches: tuple[str, ...]
    failed_task_count: int
    claude_usage_limit_wait_occurred: bool
    codex_usage_limit_wait_occurred: bool
    gemini_usage_limit_wait_occurred: bool


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

    def __init__(
        self,
        session_factory: SessionFactory,
        config: AppConfig,
        *,
        actor: TaskCreator | str = TaskCreator.USER,
        actor_session_id: str | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._actor = TaskCreator(actor)
        self._actor_session_id = _normalize_session_id(actor_session_id)

    def list(
        self,
        *,
        status: TaskStatus | str | None = None,
        project_path: Path | str | None = None,
        created_by: TaskCreator | str | None = None,
        creator_session_id: str | None = None,
        active: bool = False,
    ) -> list[TaskView]:
        if active and status is not None:
            raise ValueError("status and active filters cannot be combined")
        archived_only = status == "archived"
        normalized_status = None if status is None or archived_only else TaskStatus(status)
        canonical_path = (
            None
            if project_path is None
            else str(self._canonical_project_path(project_path))
        )
        normalized_creator = None if created_by is None else TaskCreator(created_by)
        normalized_session = _normalize_session_id(creator_session_id)
        with self._session_factory() as session:
            tasks = TaskRepository(session).list(
                status=normalized_status,
                project_path=canonical_path,
                created_by=normalized_creator,
                creator_session_id=normalized_session,
            )
            if archived_only:
                tasks = [task for task in tasks if _is_archived(task)]
            else:
                tasks = [task for task in tasks if not _is_archived(task)]
            if active:
                tasks = [task for task in tasks if task.status in _ACTIVE_STATUSES]
            return [_to_task_view(task) for task in tasks]

    def get(self, task_id: int) -> TaskView | None:
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            return None if task is None else _to_task_view(task)

    def pending_reviews(
        self,
        *,
        project_path: Path | str | None = None,
    ) -> list[PendingReviewView]:
        canonical_path = (
            None
            if project_path is None
            else str(self._canonical_project_path(project_path))
        )
        with self._session_factory() as session:
            tasks = TaskRepository(session).list_pending_reviews(
                project_path=canonical_path
            )
            return [
                PendingReviewView(
                    id=task.id,
                    title=task.title,
                    project_path=task.project_path,
                    finished_at=task.finished_at,
                )
                for task in tasks
            ]

    def review_task(
        self,
        task_id: int,
        *,
        verdict: ReviewVerdict | str,
        score: int | None = None,
        notes: str | None = None,
        reopen_prompt: str | None = None,
    ) -> TaskReviewResultView:
        normalized_verdict = ReviewVerdict(verdict)
        if score is not None and not 1 <= score <= 5:
            raise ValueError("review score must be between 1 and 5")
        if reopen_prompt is not None:
            if normalized_verdict is not ReviewVerdict.FAIL:
                raise ValueError("--reopen can only be used with verdict fail")
            if not reopen_prompt.strip():
                raise ValueError("--reopen prompt must not be empty")

        with self._session_factory() as session:
            task_repo = TaskRepository(session)
            review_repo = TaskReviewRepository(session)
            task = self._require_task(task_repo, task_id)
            if task.status is not TaskStatus.SUCCEEDED:
                raise ValueError("only succeeded tasks can be reviewed")
            previous_reviews = review_repo.list_for_task(task_id)
            review = review_repo.add(
                TaskReview(
                    task_id=task.id,
                    verdict=normalized_verdict,
                    score=score,
                    notes=notes.strip() if notes and notes.strip() else None,
                    reviewed_at=utcnow(),
                )
            )
            rework_task = None
            if reopen_prompt is not None:
                rework_task = task_repo.add(
                    Task(
                        title=f"Rework: {task.title}",
                        prompt=reopen_prompt.strip(),
                        project_path=task.project_path,
                        created_by=self._actor,
                        creator_session_id=self._actor_session_id,
                        agent_policy=task.agent_policy,
                        priority=task.priority,
                        estimated_tokens=task.estimated_tokens,
                        approval_mode=task.approval_mode,
                        auto_commit=task.auto_commit,
                        draft_pr=task.draft_pr,
                        max_retries=task.max_retries,
                        retry_delay_minutes=task.retry_delay_minutes,
                        tags=[tag for tag in task.tags if tag != _ARCHIVED_TAG],
                        rework_of_task_id=task.id,
                    )
                )
                self._record_event(
                    session,
                    rework_task,
                    TaskEventAction.CREATED,
                    {"rework_of_task_id": task.id},
                )
            self._record_event(
                session,
                task,
                TaskEventAction.REVIEWED,
                {
                    "verdict": normalized_verdict.value,
                    "score": score,
                    "rework_task_id": None if rework_task is None else rework_task.id,
                },
            )
            session.commit()
            return TaskReviewResultView(
                task=_to_task_view(task),
                review=_to_review_view(review, len(previous_reviews) + 1),
                previous_review_count=len(previous_reviews),
                rework_task=(
                    None if rework_task is None else _to_task_view(rework_task)
                ),
            )

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
                gemini_run_count=sum(
                    project.gemini_run_count for project in project_views
                ),
                fallback_count=sum(project.fallback_count for project in project_views),
                worktree_branches=all_branches,
                failed_task_count=sum(
                    len(project.failed_tasks) for project in project_views
                ),
                claude_usage_limit_wait_occurred=usage_limit_wait[AgentName.CLAUDE],
                codex_usage_limit_wait_occurred=usage_limit_wait[AgentName.CODEX],
                gemini_usage_limit_wait_occurred=usage_limit_wait[AgentName.GEMINI],
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
            for task in tasks:
                self._record_event(session, task, TaskEventAction.CREATED)
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
            for child in children:
                self._record_event(
                    session,
                    child,
                    TaskEventAction.CREATED,
                    {"split_parent_task_id": original.id},
                )
            for dependent in dependents:
                dependent.depends_on.remove(original)
                for child in children:
                    repo.add_dependency(dependent, child)
            self._record_event(
                session,
                original,
                TaskEventAction.SPLIT,
                {
                    "child_task_ids": [child.id for child in children],
                    "rewired_dependent_ids": list(dependent_ids),
                },
            )
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
            values = _task_values(
                draft,
                canonical_path,
                default_creator=task.created_by,
                default_session_id=task.creator_session_id,
            )
            changed_fields = sorted(
                field for field, value in values.items() if getattr(task, field) != value
            )
            for field, value in values.items():
                setattr(task, field, value)
            if project_changed:
                task.use_worktree = False
                task.worktree_path = None
                task.worktree_branch = None
            if draft.agent_policy is not AgentPolicy.ANY:
                task.fallback_agent = None
            task.depends_on = dependencies
            task.updated_at = utcnow()
            self._record_event(
                session,
                task,
                TaskEventAction.UPDATED,
                {"fields": changed_fields},
            )
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
                auto_commit=source.auto_commit,
                draft_pr=source.draft_pr,
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
            self._record_event(session, task, TaskEventAction.CANCELLED)
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
            self._record_event(session, task, TaskEventAction.RETRIED)
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
            self._record_event(session, task, TaskEventAction.PROMOTED)
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
                self._record_event(session, task, TaskEventAction.ARCHIVED)
                session.commit()
            return _to_task_view(task)

    def delete(self, task_id: int) -> None:
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = self._require_task(repo, task_id)
            reason = _deletion_block_reason(task)
            if reason is not None:
                raise ValueError(reason)
            self._delete_in_session(session, repo, task)
            session.commit()

    def delete_project_tasks(
        self,
        project_path: Path | str,
        *,
        created_by: TaskCreator | str | None = None,
        creator_session_id: str | None = None,
        statuses: list[TaskStatus | str] | None = None,
    ) -> BulkDeleteResultView:
        canonical_path = str(self._canonical_project_path(project_path))
        normalized_creator = None if created_by is None else TaskCreator(created_by)
        normalized_session = _normalize_session_id(creator_session_id)
        status_values = (
            None
            if statuses is None
            else {str(getattr(status, "value", status)) for status in statuses}
        )
        normalized_statuses = (
            set()
            if status_values is None
            else {
                TaskStatus(status)
                for status in status_values
                if status != "archived"
            }
        )
        with self._session_factory() as session:
            repo = TaskRepository(session)
            candidates = repo.list(
                project_path=canonical_path,
                created_by=normalized_creator,
                creator_session_id=normalized_session,
            )
            if status_values is not None:
                candidates = [
                    task
                    for task in candidates
                    if (
                        (_is_archived(task) and "archived" in status_values)
                        or (
                            not _is_archived(task)
                            and task.status in normalized_statuses
                        )
                    )
                ]
            remaining = {task.id for task in candidates}
            deleted_ids: list[int] = []
            while remaining:
                deleted_one = False
                for task_id in sorted(remaining, reverse=True):
                    task = repo.get(task_id)
                    if task is None:
                        remaining.remove(task_id)
                        continue
                    if _deletion_block_reason(task) is not None:
                        continue
                    self._delete_in_session(session, repo, task)
                    session.flush()
                    deleted_ids.append(task_id)
                    remaining.remove(task_id)
                    session.expire_all()
                    deleted_one = True
                    break
                if not deleted_one:
                    break
            skipped = []
            for task_id in sorted(remaining):
                task = repo.get(task_id)
                if task is None:
                    continue
                skipped.append(
                    BulkDeleteSkippedView(
                        id=task.id,
                        title=task.title,
                        reason=_deletion_block_reason(task) or "task could not be deleted",
                    )
                )
            session.commit()
            return BulkDeleteResultView(
                project_path=canonical_path,
                matched_count=len(candidates),
                deleted_ids=tuple(sorted(deleted_ids)),
                skipped=tuple(skipped),
            )

    def list_runs(self, task_id: int) -> list[RunView]:
        with self._session_factory() as session:
            if TaskRepository(session).get(task_id) is None:
                raise KeyError(f"task {task_id} not found")
            return [
                _to_run_view(run)
                for run in TaskRunRepository(session).list_for_task(task_id)
            ]

    def list_events(
        self,
        task_id: int | None = None,
        *,
        project_path: Path | str | None = None,
        actor: TaskCreator | str | None = None,
        limit: int = 100,
    ) -> list[TaskEventView]:
        if limit < 1 or limit > 1000:
            raise ValueError("event limit must be between 1 and 1000")
        canonical_path = (
            None
            if project_path is None
            else str(self._canonical_project_path(project_path))
        )
        normalized_actor = None if actor is None else TaskCreator(actor)
        with self._session_factory() as session:
            task_created_at = None
            if task_id is not None:
                task = TaskRepository(session).get(task_id)
                task_created_at = None if task is None else task.created_at
            events = TaskEventRepository(session).list(
                task_id=task_id,
                task_created_at=task_created_at,
                project_path=canonical_path,
                actor=normalized_actor,
                limit=limit,
            )
            return [_to_event_view(event) for event in events]

    def _delete_in_session(
        self,
        session: Session,
        repo: TaskRepository,
        task: Task,
    ) -> None:
        self._record_event(session, task, TaskEventAction.DELETED)
        session.execute(sql_delete(TaskRun).where(TaskRun.task_id == task.id))
        repo.delete(task)

    def _record_event(
        self,
        session: Session,
        task: Task,
        action: TaskEventAction,
        details: dict[str, object] | None = None,
    ) -> None:
        TaskEventRepository(session).add(
            TaskEvent(
                task_id=task.id,
                task_created_at=task.created_at,
                action=action,
                actor=self._actor,
                actor_session_id=self._actor_session_id,
                project_path=task.project_path,
                task_title=task.title,
                details={} if details is None else details,
            )
        )

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

    def _add_batch(
        self,
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
                    **_task_values(
                        draft,
                        canonical_path,
                        default_creator=self._actor,
                        default_session_id=self._actor_session_id,
                    ),
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


def _task_values(
    draft: TaskDraft,
    canonical_path: Path,
    *,
    default_creator: TaskCreator,
    default_session_id: str | None,
) -> dict[str, object]:
    created_by = draft.created_by or default_creator
    creator_session_id = (
        draft.creator_session_id
        if draft.creator_session_id is not None
        else default_session_id
    )
    return {
        "title": draft.title,
        "prompt": draft.prompt,
        "project_path": str(canonical_path),
        "created_by": created_by,
        "creator_session_id": creator_session_id,
        "agent_policy": draft.agent_policy,
        "priority": draft.priority,
        "estimated_tokens": draft.estimated_tokens,
        "approval_mode": draft.approval_mode,
        "auto_commit": draft.auto_commit,
        "draft_pr": draft.draft_pr,
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
    latest_review = task.reviews[-1] if task.reviews else None
    return TaskView(
        id=task.id,
        title=task.title,
        prompt=task.prompt,
        project_path=task.project_path,
        created_by=task.created_by,
        creator_session_id=task.creator_session_id,
        agent_policy=task.agent_policy,
        assigned_agent=task.assigned_agent,
        fallback_agent=task.fallback_agent,
        priority=task.priority,
        status=task.status,
        approval_mode=task.approval_mode,
        auto_commit=task.auto_commit,
        draft_pr=task.draft_pr,
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
        rework_of_task_id=task.rework_of_task_id,
        rework_task_ids=tuple(child.id for child in task.rework_tasks),
        review_verdict=None if latest_review is None else latest_review.verdict,
        review_score=None if latest_review is None else latest_review.score,
        review_notes=None if latest_review is None else latest_review.notes,
        reviewed_at=None if latest_review is None else latest_review.reviewed_at,
        review_count=len(task.reviews),
        archived=_is_archived(task),
        use_worktree=task.use_worktree,
        worktree_path=task.worktree_path,
        worktree_branch=task.worktree_branch,
        estimated_tokens=task.estimated_tokens,
    )


def _to_review_view(review: TaskReview, revision: int) -> ReviewView:
    return ReviewView(
        id=review.id,
        task_id=review.task_id,
        verdict=review.verdict,
        score=review.score,
        notes=review.notes,
        reviewed_at=review.reviewed_at,
        revision=revision,
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
        gemini_run_count=value.agent_run_counts[AgentName.GEMINI],
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
        auto_commit_hash=run.auto_commit_hash,
        auto_commit_result=run.auto_commit_result,
        draft_pr_url=run.draft_pr_url,
        draft_pr_result=run.draft_pr_result,
    )


def _to_event_view(event: TaskEvent) -> TaskEventView:
    return TaskEventView(
        id=event.id,
        task_id=event.task_id,
        task_created_at=event.task_created_at,
        action=event.action,
        actor=event.actor,
        actor_session_id=event.actor_session_id,
        project_path=event.project_path,
        task_title=event.task_title,
        details=dict(event.details),
        created_at=event.created_at,
    )


def _normalize_session_id(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("creator session ID must not be empty")
    if len(normalized) > 256:
        raise ValueError("creator session ID must be at most 256 characters")
    return normalized


def _deletion_block_reason(task: Task) -> str | None:
    if task.status in {TaskStatus.RESERVED, TaskStatus.RUNNING}:
        return "reserved or running tasks must be cancelled before deletion"
    if task.worktree_path and Path(task.worktree_path).exists():
        return "clean up the task worktree before deleting its history"
    relations = (
        ("required by dependent tasks", sorted(item.id for item in task.dependents)),
        ("the split parent of child tasks", [item.id for item in task.split_children]),
        ("the rework source of tasks", [item.id for item in task.rework_tasks]),
    )
    for label, related_ids in relations:
        if related_ids:
            joined_ids = ", ".join(str(task_id) for task_id in related_ids)
            return f"task {task.id} is {label}: {joined_ids}"
    return None
