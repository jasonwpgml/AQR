"""Candidate filtering, deterministic task selection, and atomic reservation."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    TaskStatus,
)
from ai_queue_runner.domain.models import AgentState, Task
from ai_queue_runner.domain.scoring import calculate_score
from ai_queue_runner.infrastructure.repositories import AgentStateRepository

LOCKING_STATUSES = frozenset({TaskStatus.RESERVED, TaskStatus.RUNNING})
UNBLOCKED_DEPENDENT_STATUSES = frozenset({TaskStatus.SUCCEEDED, TaskStatus.CANCELLED})

# Statuses the reservation query must load: waiting tasks are candidates;
# reserved/running tasks are needed to compute project locks.
_RESERVATION_SCOPE = (TaskStatus.WAITING, TaskStatus.RESERVED, TaskStatus.RUNNING)


def reserve_next_task(
    session: Session,
    config: AppConfig,
    agent: AgentName,
    now: datetime | None = None,
    max_attempts: int = 3,
) -> Task | None:
    """Atomically pick and reserve the best task for ``agent`` (plan §5.4).

    Selection and reservation happen in one transaction, and this function owns
    the commit. Duplicate assignment is prevented by compare-and-swap UPDATEs:
    the task row is claimed only if still ``waiting``, the agent row only if it
    holds no other task. A lost race rolls back and retries, up to
    ``max_attempts`` times. Returns the reserved task or None.
    """
    scheduler = SchedulerService(config)
    for _ in range(max_attempts):
        try:
            now_ = utcnow() if now is None else now
            state = AgentStateRepository(session).get_or_create(agent)
            tasks = session.scalars(
                select(Task).where(Task.status.in_(_RESERVATION_SCOPE))
            ).all()
            candidate = scheduler.select_task(tasks, agent, state, now_)
            if candidate is None:
                session.rollback()
                return None

            task_claimed = session.execute(
                update(Task)
                .where(Task.id == candidate.id, Task.status == TaskStatus.WAITING)
                .values(
                    status=TaskStatus.RESERVED,
                    assigned_agent=agent,
                    reserved_at=now_,
                    updated_at=now_,
                )
                .execution_options(synchronize_session=False)
            ).rowcount
            # AIDEV-NOTE: current_task_id is set at reservation (not run start) so an
            # agent can never hold two reservations; recovery/execution must clear it.
            agent_claimed = session.execute(
                update(AgentState)
                .where(AgentState.agent == agent, AgentState.current_task_id.is_(None))
                .values(current_task_id=candidate.id)
                .execution_options(synchronize_session=False)
            ).rowcount

            if task_claimed == 1 and agent_claimed == 1:
                session.commit()
                session.refresh(candidate)
                return candidate
            session.rollback()  # lost the race; re-select on next attempt
        except OperationalError:
            session.rollback()  # SQLite write-lock contention; retry briefly
    return None


class SchedulerService:
    """Select the best eligible task for one agent without mutating persistence."""

    def __init__(self, config: AppConfig) -> None:
        self._config = config

    def select_task(
        self,
        tasks: Iterable[Task],
        agent: AgentName,
        state: AgentState | None,
        now: datetime | None = None,
    ) -> Task | None:
        """Return the highest-ranked candidate, leaving reservation to the caller."""
        now = utcnow() if now is None else now
        if not self._agent_is_available(agent, state, now):
            return None

        task_list = list(tasks)
        locked_projects = {
            task.project_path for task in task_list if task.status in LOCKING_STATUSES
        }
        ranked: list[tuple[tuple[object, ...], Task]] = []

        for task in task_list:
            if not self._is_candidate(task, agent, now, locked_projects):
                continue
            blocked_dependents_count = sum(
                dependent.status not in UNBLOCKED_DEPENDENT_STATUSES
                for dependent in getattr(task, "dependents", ())
            )
            score = calculate_score(
                task,
                agent,
                now,
                self._config.scheduler,
                blocked_dependents_count=blocked_dependents_count,
            )
            if score is not None:
                ranked.append((self._rank_key(task, agent, now, score), task))

        return min(ranked, default=((), None), key=lambda item: item[0])[1]

    def _agent_is_available(
        self,
        agent: AgentName,
        state: AgentState | None,
        now: datetime,
    ) -> bool:
        settings = self._config.agents.get(agent.value)
        return bool(
            settings is not None
            and settings.enabled
            and state is not None
            and state.agent is agent
            and state.status is AgentStatus.AVAILABLE
            and state.current_task_id is None
            and (state.available_at is None or state.available_at <= now)
        )

    @staticmethod
    def _is_candidate(
        task: Task,
        agent: AgentName,
        now: datetime,
        locked_projects: set[str],
    ) -> bool:
        return bool(
            task.status is TaskStatus.WAITING
            and (task.not_before is None or task.not_before <= now)
            and task.agent_policy in (AgentPolicy(agent.value), AgentPolicy.ANY)
            and task.retry_count <= task.max_retries
            and all(dependency.status is TaskStatus.SUCCEEDED for dependency in task.depends_on)
            and task.project_path not in locked_projects
        )

    def _rank_key(
        self,
        task: Task,
        agent: AgentName,
        now: datetime,
        score: float,
    ) -> tuple[object, ...]:
        dedicated = task.agent_policy is AgentPolicy(agent.value)
        # AIDEV-NOTE: §5.5 dedicated preemption intentionally ranks before numeric score;
        # otherwise a high-scoring Any task can still consume every agent slot.
        preempt_any = (
            dedicated and task.priority >= self._config.scheduler.dedicated_priority_preempt
        )
        eligible_since = max(task.created_at, task.not_before or task.created_at)
        waiting_seconds = max(0.0, (now - eligible_since).total_seconds())
        task_id = task.id if task.id is not None else float("inf")
        return (
            -int(preempt_any),
            -score,
            -int(dedicated),
            -task.priority,
            -waiting_seconds,
            task.created_at,
            task_id,
        )
