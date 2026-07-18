"""Candidate filtering, deterministic task selection, and atomic reservation."""

from __future__ import annotations

from collections.abc import Collection, Iterable
from datetime import datetime

from sqlalchemy import exists, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, aliased

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
    eligible_agents: Collection[AgentName] | None = None,
    exclude_task_ids: Collection[int] = (),
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
            state_repo = AgentStateRepository(session)
            states = {
                candidate_agent: state_repo.get_or_create(candidate_agent)
                for candidate_agent in AgentName
            }
            available_agents = scheduler.available_agents(
                states,
                now_,
                eligible_agents=eligible_agents,
            )
            tasks_stmt = select(Task).where(Task.status.in_(_RESERVATION_SCOPE))
            if exclude_task_ids:
                tasks_stmt = tasks_stmt.where(Task.id.not_in(exclude_task_ids))
            tasks = session.scalars(tasks_stmt).all()
            candidate = scheduler.select_task(
                tasks,
                agent,
                states[agent],
                now_,
                available_agents=available_agents,
            )
            if candidate is None:
                session.rollback()
                return None
            reserve_isolated = candidate.use_worktree or (
                config.scheduler.worktree_parallel_enabled
                and _project_is_locked(tasks, candidate)
            )
            if _claim_task(
                session,
                candidate,
                agent,
                now_,
                reserve_isolated=reserve_isolated,
            ):
                return candidate
            session.rollback()  # lost the race; re-select on next attempt
        except OperationalError:
            session.rollback()  # SQLite write-lock contention; retry briefly
    return None


def reserve_specific_task(
    session: Session,
    config: AppConfig,
    task_id: int,
    agent: AgentName,
    now: datetime | None = None,
    eligible_agents: Collection[AgentName] | None = None,
) -> Task | None:
    """Atomically reserve one user-selected task if it is eligible for ``agent``."""
    now = utcnow() if now is None else now
    try:
        scheduler = SchedulerService(config)
        state_repo = AgentStateRepository(session)
        states = {
            candidate_agent: state_repo.get_or_create(candidate_agent)
            for candidate_agent in AgentName
        }
        available_agents = scheduler.available_agents(
            states,
            now,
            eligible_agents=eligible_agents,
        )
        task = session.get(Task, task_id)
        if task is None:
            session.rollback()
            return None
        locking_tasks = session.scalars(
            select(Task).where(
                Task.status.in_(LOCKING_STATUSES),
                Task.id != task_id,
            )
        ).all()
        candidate = scheduler.select_task(
            [task, *locking_tasks],
            agent,
            states[agent],
            now,
            available_agents=available_agents,
        )
        if candidate is None or candidate.id != task_id:
            session.rollback()
            return None
        reserve_isolated = candidate.use_worktree or (
            config.scheduler.worktree_parallel_enabled
            and _project_is_locked(locking_tasks, candidate)
        )
        if _claim_task(
            session,
            candidate,
            agent,
            now,
            reserve_isolated=reserve_isolated,
        ):
            return candidate
        session.rollback()
    except OperationalError:
        session.rollback()
    return None


def _claim_task(
    session: Session,
    candidate: Task,
    agent: AgentName,
    now: datetime,
    *,
    reserve_isolated: bool,
) -> bool:
    locking_task = aliased(Task)
    project_lock_exists = exists().where(
        locking_task.id != candidate.id,
        locking_task.project_path == candidate.project_path,
        locking_task.status.in_(LOCKING_STATUSES),
    )
    claim_conditions = [Task.id == candidate.id, Task.status == TaskStatus.WAITING]
    preexisting_isolation_request = candidate.use_worktree
    if not preexisting_isolation_request:
        # AIDEV-NOTE: computed isolation is CAS-checked against the live lock state;
        # an already-isolated task skips this branch because it does not need a conflict.
        claim_conditions.append(
            project_lock_exists if reserve_isolated else ~project_lock_exists
        )
    task_claimed = session.execute(
        update(Task)
        .where(*claim_conditions)
        .values(
            status=TaskStatus.RESERVED,
            assigned_agent=agent,
            reserved_at=now,
            updated_at=now,
            use_worktree=reserve_isolated,
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
    if task_claimed != 1 or agent_claimed != 1:
        return False
    session.commit()
    session.refresh(candidate)
    return True


def _project_is_locked(tasks: Iterable[Task], candidate: Task) -> bool:
    return candidate.project_path in _locked_project_paths(
        task for task in tasks if task.id != candidate.id
    )


def _locked_project_paths(tasks: Iterable[Task]) -> set[str]:
    return {task.project_path for task in tasks if task.status in LOCKING_STATUSES}


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
        *,
        available_agents: Collection[AgentName] | None = None,
    ) -> Task | None:
        """Return the highest-ranked candidate, leaving reservation to the caller."""
        now = utcnow() if now is None else now
        if not self._agent_is_available(agent, state, now):
            return None
        available_agents = {agent} if available_agents is None else set(available_agents)

        task_list = list(tasks)
        locked_projects = _locked_project_paths(task_list)
        ranked: list[tuple[tuple[object, ...], Task]] = []

        for task in task_list:
            if not self._is_candidate(
                task,
                agent,
                now,
                locked_projects,
                available_agents,
            ):
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

    def available_agents(
        self,
        states: dict[AgentName, AgentState],
        now: datetime,
        *,
        eligible_agents: Collection[AgentName] | None = None,
    ) -> frozenset[AgentName]:
        eligible = set(AgentName) if eligible_agents is None else set(eligible_agents)
        return frozenset(
            agent
            for agent, state in states.items()
            if agent in eligible and self._agent_is_available(agent, state, now)
        )

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

    def _is_candidate(
        self,
        task: Task,
        agent: AgentName,
        now: datetime,
        locked_projects: set[str],
        available_agents: set[AgentName],
    ) -> bool:
        return bool(
            task.status is TaskStatus.WAITING
            and (task.not_before is None or task.not_before <= now)
            and task.agent_policy in (AgentPolicy(agent.value), AgentPolicy.ANY)
            and task.retry_count <= task.max_retries
            and all(dependency.status is TaskStatus.SUCCEEDED for dependency in task.depends_on)
            and (
                task.project_path not in locked_projects
                or task.use_worktree
                or self._config.scheduler.worktree_parallel_enabled
            )
            and not self._fallback_is_preferred_elsewhere(
                task,
                agent,
                available_agents,
            )
        )

    def _fallback_is_preferred_elsewhere(
        self,
        task: Task,
        agent: AgentName,
        available_agents: set[AgentName],
    ) -> bool:
        fallback_agent = task.fallback_agent
        return bool(
            self._config.scheduler.fallback_enabled
            and task.agent_policy is AgentPolicy.ANY
            and fallback_agent is not None
            and fallback_agent is not agent
            and fallback_agent in available_agents
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
