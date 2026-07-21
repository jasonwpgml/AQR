"""Session-scoped manual agent availability management."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus
from ai_queue_runner.domain.models import AgentState
from ai_queue_runner.infrastructure.repositories import AgentStateRepository

SessionFactory = Callable[[], Session]


@dataclass(frozen=True)
class AgentView:
    agent: AgentName
    status: AgentStatus
    available_at: datetime | None
    current_task_id: int | None
    last_checked_at: datetime | None
    last_error: str | None


class AgentService:
    """Expose agent state without leaking session-bound ORM rows to the UI."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list(self) -> list[AgentView]:
        with self._session_factory() as session:
            repo = AgentStateRepository(session)
            states = [repo.get_or_create(agent) for agent in AgentName]
            session.commit()
            return [_to_agent_view(state) for state in states]

    def set_manual_availability(
        self,
        agent: AgentName | str,
        available_at: datetime | None,
        *,
        now: datetime | None = None,
    ) -> AgentView:
        now = utcnow() if now is None else now
        status = (
            AgentStatus.COOLDOWN
            if available_at is not None and available_at > now
            else AgentStatus.AVAILABLE
        )
        return self.set_availability(agent, status, available_at, now=now)

    def set_availability(
        self,
        agent: AgentName | str,
        status: AgentStatus | str,
        available_at: datetime | None = None,
        *,
        now: datetime | None = None,
    ) -> AgentView:
        normalized_agent = AgentName(agent)
        normalized_status = AgentStatus(status)
        if normalized_status not in {
            AgentStatus.AVAILABLE,
            AgentStatus.COOLDOWN,
            AgentStatus.DISABLED,
        }:
            raise ValueError("manual availability must be available, cooldown, or disabled")
        if available_at is not None and available_at.tzinfo is not None:
            raise ValueError("available_at must be a naive UTC datetime")
        now = utcnow() if now is None else now
        if normalized_status is AgentStatus.COOLDOWN and available_at is not None:
            if available_at <= now:
                normalized_status = AgentStatus.AVAILABLE
                available_at = None
        elif normalized_status is not AgentStatus.COOLDOWN:
            available_at = None

        with self._session_factory() as session:
            state = AgentStateRepository(session).get_or_create(normalized_agent)
            if state.current_task_id is not None or state.status is AgentStatus.BUSY:
                raise ValueError(f"cannot change availability while {normalized_agent} is busy")
            state.status = normalized_status
            state.available_at = available_at
            state.last_checked_at = now
            state.last_error = None
            session.commit()
            return _to_agent_view(state)

    def promote_due_cooldowns(self, now: datetime | None = None) -> list[AgentView]:
        now = utcnow() if now is None else now
        with self._session_factory() as session:
            due = list(
                session.scalars(
                    select(AgentState)
                    .where(
                        AgentState.status == AgentStatus.COOLDOWN,
                        AgentState.available_at.is_not(None),
                        AgentState.available_at <= now,
                    )
                    .order_by(AgentState.agent)
                )
            )
            for state in due:
                state.status = AgentStatus.AVAILABLE
                state.available_at = None
                state.last_checked_at = now
                state.last_error = None
            if due:
                session.commit()
            else:
                session.rollback()
            return [_to_agent_view(state) for state in due]


def _to_agent_view(state: AgentState) -> AgentView:
    return AgentView(
        agent=state.agent,
        status=state.status,
        available_at=state.available_at,
        current_task_id=state.current_task_id,
        last_checked_at=state.last_checked_at,
        last_error=state.last_error,
    )
