from datetime import timedelta

import pytest

from ai_queue_runner.application.recovery_service import release_stale_reservations
from ai_queue_runner.application.scheduler_service import reserve_next_task
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus, TaskStatus
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    TaskRepository,
)


@pytest.fixture()
def config():
    return AppConfig()


@pytest.fixture()
def session(tmp_path):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


def reserve_one(session, config, agent=AgentName.CLAUDE) -> Task:
    state = AgentStateRepository(session).get_or_create(agent)
    state.status = AgentStatus.AVAILABLE
    TaskRepository(session).add(
        Task(title="t", prompt="p", project_path="C:/projects/demo", agent_policy="any")
    )
    session.commit()
    reserved = reserve_next_task(session, config, agent)
    assert reserved is not None
    return reserved


def backdate(session, task: Task, minutes: int) -> None:
    task.reserved_at = utcnow() - timedelta(minutes=minutes)
    session.commit()


def test_stale_reservation_is_released(session, config):
    task = reserve_one(session, config)
    backdate(session, task, minutes=config.scheduler.reserved_timeout_minutes + 1)

    released = release_stale_reservations(session, config)

    assert [t.id for t in released] == [task.id]
    assert task.status is TaskStatus.WAITING
    assert task.assigned_agent is None
    assert task.reserved_at is None
    state = AgentStateRepository(session).get(AgentName.CLAUDE)
    assert state.current_task_id is None


def test_fresh_reservation_is_kept(session, config):
    task = reserve_one(session, config)

    assert release_stale_reservations(session, config) == []
    assert task.status is TaskStatus.RESERVED
    assert AgentStateRepository(session).get(AgentName.CLAUDE).current_task_id == task.id


def test_released_task_can_be_reserved_again(session, config):
    task = reserve_one(session, config)
    backdate(session, task, minutes=config.scheduler.reserved_timeout_minutes + 1)
    release_stale_reservations(session, config)

    again = reserve_next_task(session, config, AgentName.CLAUDE)
    assert again is not None and again.id == task.id
    assert again.status is TaskStatus.RESERVED
