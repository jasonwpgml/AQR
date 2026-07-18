"""SQLAlchemy ORM models for queue state, execution history, and reusable drafts.

Enum columns are stored as their string values in TEXT (readable with any SQLite
browser). Python-side @validates rejects bad values at assignment time; CHECK
constraints back this up at the DB level.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    mapped_column,
    relationship,
    validates,
)

from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    ErrorType,
    ReviewVerdict,
    TaskStatus,
)


def _enum(enum_cls: type) -> SAEnum:
    """TEXT column constrained to the enum's values (not member names)."""
    return SAEnum(
        enum_cls,
        values_callable=lambda e: [m.value for m in e],
        native_enum=False,
        validate_strings=True,
    )


class Base(DeclarativeBase):
    pass


class TaskDependency(Base):
    __tablename__ = "task_dependencies"

    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )
    depends_on_task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), primary_key=True
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 0 AND 100", name="ck_tasks_priority_range"),
        CheckConstraint(
            "estimated_tokens >= 0", name="ck_tasks_estimated_tokens_nonnegative"
        ),
        CheckConstraint(
            "recurrence_interval_days IS NULL OR recurrence_interval_days >= 1",
            name="ck_tasks_recurrence_interval_positive",
        ),
        UniqueConstraint(
            "recurrence_source_task_id",
            name="uq_tasks_recurrence_source_task_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    project_path: Mapped[str] = mapped_column(Text)
    agent_policy: Mapped[AgentPolicy] = mapped_column(_enum(AgentPolicy))
    assigned_agent: Mapped[AgentName | None] = mapped_column(_enum(AgentName), default=None)
    fallback_agent: Mapped[AgentName | None] = mapped_column(_enum(AgentName), default=None)
    priority: Mapped[int] = mapped_column(default=50)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[TaskStatus] = mapped_column(_enum(TaskStatus), default=TaskStatus.WAITING)
    approval_mode: Mapped[ApprovalMode] = mapped_column(
        _enum(ApprovalMode), default=ApprovalMode.CONFIRM
    )
    max_retries: Mapped[int] = mapped_column(default=2)
    retry_count: Mapped[int] = mapped_column(default=0)
    retry_delay_minutes: Mapped[int] = mapped_column(default=10)
    recurrence_interval_days: Mapped[int | None] = mapped_column(Integer, default=None)
    recurrence_source_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"), default=None
    )
    split_parent_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), index=True, default=None
    )
    rework_of_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="RESTRICT"), index=True, default=None
    )
    not_before: Mapped[datetime | None] = mapped_column(default=None)
    reserved_at: Mapped[datetime | None] = mapped_column(default=None)
    started_at: Mapped[datetime | None] = mapped_column(default=None)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    tags: Mapped[list[str]] = mapped_column("tags_json", JSON, default=list)
    use_worktree: Mapped[bool] = mapped_column(Boolean, default=False)
    worktree_path: Mapped[str | None] = mapped_column(Text, default=None)
    worktree_branch: Mapped[str | None] = mapped_column(Text, default=None)

    depends_on: Mapped[list[Task]] = relationship(
        secondary=TaskDependency.__table__,
        primaryjoin=id == TaskDependency.task_id,
        secondaryjoin=id == TaskDependency.depends_on_task_id,
        backref="dependents",
    )
    split_parent: Mapped[Task | None] = relationship(
        "Task",
        foreign_keys=[split_parent_task_id],
        remote_side=[id],
        back_populates="split_children",
    )
    split_children: Mapped[list[Task]] = relationship(
        "Task",
        foreign_keys=[split_parent_task_id],
        back_populates="split_parent",
        order_by="Task.id",
    )
    rework_of: Mapped[Task | None] = relationship(
        "Task",
        foreign_keys=[rework_of_task_id],
        remote_side=[id],
        back_populates="rework_tasks",
    )
    rework_tasks: Mapped[list[Task]] = relationship(
        "Task",
        foreign_keys=[rework_of_task_id],
        back_populates="rework_of",
        order_by="Task.id",
    )
    runs: Mapped[list[TaskRun]] = relationship(back_populates="task")
    reviews: Mapped[list[TaskReview]] = relationship(
        back_populates="task",
        order_by="TaskReview.id",
        cascade="all, delete-orphan",
    )

    @validates("agent_policy")
    def _coerce_policy(self, _key: str, value: object) -> AgentPolicy:
        return AgentPolicy(value)

    @validates("status")
    def _coerce_status(self, _key: str, value: object) -> TaskStatus:
        # AIDEV-NOTE: coercion only — transition legality lives in
        # domain/transitions.py and is enforced by TaskRepository.set_status().
        return TaskStatus(value)

    @validates("approval_mode")
    def _coerce_approval(self, _key: str, value: object) -> ApprovalMode:
        return ApprovalMode(value)

    @validates("priority")
    def _check_priority(self, _key: str, value: int) -> int:
        if not 0 <= value <= 100:
            raise ValueError(f"priority must be 0..100, got {value}")
        return value


class AgentState(Base):
    __tablename__ = "agent_states"

    agent: Mapped[AgentName] = mapped_column(_enum(AgentName), primary_key=True)
    status: Mapped[AgentStatus] = mapped_column(
        _enum(AgentStatus), default=AgentStatus.UNKNOWN
    )
    available_at: Mapped[datetime | None] = mapped_column(default=None)
    current_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), default=None)
    last_checked_at: Mapped[datetime | None] = mapped_column(default=None)
    last_error: Mapped[str | None] = mapped_column(Text, default=None)


class TaskRun(Base):
    __tablename__ = "task_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"))
    agent: Mapped[AgentName] = mapped_column(_enum(AgentName))
    attempt: Mapped[int]
    command: Mapped[list[str]] = mapped_column("command_json", JSON)
    pid: Mapped[int | None] = mapped_column(default=None)
    exit_code: Mapped[int | None] = mapped_column(default=None)
    stdout_path: Mapped[str | None] = mapped_column(Text, default=None)
    stderr_path: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    result_summary: Mapped[str | None] = mapped_column(Text, default=None)
    error_type: Mapped[ErrorType | None] = mapped_column(_enum(ErrorType), default=None)
    fallback_to_agent: Mapped[AgentName | None] = mapped_column(
        _enum(AgentName), default=None
    )
    worktree_path: Mapped[str | None] = mapped_column(Text, default=None)
    worktree_branch: Mapped[str | None] = mapped_column(Text, default=None)

    task: Mapped[Task] = relationship(back_populates="runs")


class TaskReview(Base):
    __tablename__ = "task_reviews"
    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR score BETWEEN 1 AND 5",
            name="ck_task_reviews_score_range",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"), index=True
    )
    verdict: Mapped[ReviewVerdict] = mapped_column(_enum(ReviewVerdict))
    score: Mapped[int | None] = mapped_column(Integer, default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    reviewed_at: Mapped[datetime] = mapped_column(default=utcnow)

    task: Mapped[Task] = relationship(back_populates="reviews")


class TaskTemplate(Base):
    __tablename__ = "task_templates"
    __table_args__ = (
        CheckConstraint(
            "priority BETWEEN 0 AND 100",
            name="ck_task_templates_priority_range",
        ),
        CheckConstraint(
            "estimated_tokens >= 0",
            name="ck_task_templates_estimated_tokens_nonnegative",
        ),
        CheckConstraint(
            "max_retries >= 0",
            name="ck_task_templates_max_retries_nonnegative",
        ),
        CheckConstraint(
            "retry_delay_minutes >= 0",
            name="ck_task_templates_retry_delay_nonnegative",
        ),
        CheckConstraint(
            "recurrence_interval_days IS NULL OR recurrence_interval_days >= 1",
            name="ck_task_templates_recurrence_interval_positive",
        ),
    )

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    project_path: Mapped[str | None] = mapped_column(Text, default=None)
    agent_policy: Mapped[AgentPolicy] = mapped_column(_enum(AgentPolicy))
    priority: Mapped[int] = mapped_column(default=50)
    estimated_tokens: Mapped[int] = mapped_column(Integer, default=0)
    approval_mode: Mapped[ApprovalMode] = mapped_column(
        _enum(ApprovalMode), default=ApprovalMode.CONFIRM
    )
    max_retries: Mapped[int] = mapped_column(default=2)
    retry_delay_minutes: Mapped[int] = mapped_column(default=10)
    recurrence_interval_days: Mapped[int | None] = mapped_column(Integer, default=None)
    not_before: Mapped[datetime | None] = mapped_column(default=None)
    tags: Mapped[list[str]] = mapped_column("tags_json", JSON, default=list)
    dependency_ids: Mapped[list[int]] = mapped_column(
        "dependency_ids_json", JSON, default=list
    )
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[object] = mapped_column("value_json", JSON)
