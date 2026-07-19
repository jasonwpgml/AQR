import threading

import pytest

from ai_queue_runner.application.scheduler_service import (
    SchedulerService,
    reserve_next_task,
)
from ai_queue_runner.config.defaults import AppConfig
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
def factory(tmp_path):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    yield make_session_factory(engine)
    engine.dispose()


def make_task(**overrides) -> Task:
    defaults = dict(
        title="task",
        prompt="do it",
        project_path="C:/projects/demo",
        agent_policy="any",
    )
    defaults.update(overrides)
    return Task(**defaults)


def set_available(session, *agents: AgentName) -> None:
    repo = AgentStateRepository(session)
    for agent in agents:
        repo.get_or_create(agent).status = AgentStatus.AVAILABLE
    session.commit()


def test_reserve_marks_task_and_agent(factory, config):
    with factory() as session:
        set_available(session, AgentName.CLAUDE)
        task = TaskRepository(session).add(make_task())
        session.commit()

        reserved = reserve_next_task(session, config, AgentName.CLAUDE)

        assert reserved is not None and reserved.id == task.id
        assert reserved.status is TaskStatus.RESERVED
        assert reserved.assigned_agent is AgentName.CLAUDE
        assert reserved.reserved_at is not None
        state = AgentStateRepository(session).get(AgentName.CLAUDE)
        assert state.current_task_id == task.id


def test_dedicated_bonus_beats_higher_priority_any(factory, config):
    # plan §2 example: claude-only P80 (+20 bonus) outranks any P90
    with factory() as session:
        set_available(session, AgentName.CLAUDE)
        repo = TaskRepository(session)
        dedicated = repo.add(
            make_task(title="claude only", agent_policy="claude", priority=80)
        )
        repo.add(make_task(title="any", priority=90, project_path="C:/projects/other"))
        session.commit()

        reserved = reserve_next_task(session, config, AgentName.CLAUDE)
        assert reserved.id == dedicated.id


def test_no_candidate_returns_none(factory, config):
    with factory() as session:
        set_available(session, AgentName.CODEX)
        TaskRepository(session).add(make_task(agent_policy="claude"))
        session.commit()
        assert reserve_next_task(session, config, AgentName.CODEX) is None


def test_gemini_reserves_dedicated_and_any_tasks(factory, config):
    config.agents["gemini"].enabled = True
    with factory() as session:
        set_available(session, AgentName.GEMINI)
        repo = TaskRepository(session)
        dedicated = repo.add(
            make_task(title="gemini only", agent_policy="gemini", project_path="C:/gemini")
        )
        any_task = repo.add(make_task(title="any", project_path="C:/any"))
        session.commit()

        assert reserve_next_task(session, config, AgentName.GEMINI).id == dedicated.id
        state = AgentStateRepository(session).get(AgentName.GEMINI)
        state.current_task_id = None
        session.commit()
        assert reserve_next_task(session, config, AgentName.GEMINI).id == any_task.id


def test_agent_cannot_hold_two_reservations(factory, config):
    with factory() as session:
        set_available(session, AgentName.CLAUDE)
        repo = TaskRepository(session)
        repo.add(make_task(title="first"))
        repo.add(make_task(title="second", project_path="C:/projects/other"))
        session.commit()

        assert reserve_next_task(session, config, AgentName.CLAUDE) is not None
        assert reserve_next_task(session, config, AgentName.CLAUDE) is None


def test_project_lock_blocks_second_agent(factory, config):
    with factory() as session:
        set_available(session, AgentName.CLAUDE, AgentName.CODEX)
        repo = TaskRepository(session)
        repo.add(make_task(title="a", agent_policy="claude", project_path="C:/projects/A"))
        repo.add(make_task(title="b", agent_policy="codex", project_path="C:/projects/A"))
        session.commit()

        assert reserve_next_task(session, config, AgentName.CLAUDE) is not None
        # same project is now locked by the reserved task
        assert reserve_next_task(session, config, AgentName.CODEX) is None


def test_same_project_lock_is_atomic_across_agents(factory, config, monkeypatch):
    with factory() as session:
        set_available(session, AgentName.CLAUDE, AgentName.CODEX)
        repo = TaskRepository(session)
        repo.add(make_task(title="a", agent_policy="claude", project_path="C:/same"))
        repo.add(make_task(title="b", agent_policy="codex", project_path="C:/same"))
        session.commit()

    selection_barrier = threading.Barrier(2)
    selection_count = 0
    count_lock = threading.Lock()
    original_select = SchedulerService.select_task

    def synchronized_select(self, *args, **kwargs):
        nonlocal selection_count
        selected = original_select(self, *args, **kwargs)
        with count_lock:
            selection_count += 1
            should_wait = selection_count <= 2
        if selected is not None and should_wait:
            selection_barrier.wait(timeout=5)
        return selected

    monkeypatch.setattr(SchedulerService, "select_task", synchronized_select)
    results: dict[AgentName, int | None] = {}

    def attempt(agent: AgentName) -> None:
        with factory() as session:
            reserved = reserve_next_task(session, config, agent)
            results[agent] = None if reserved is None else reserved.id

    threads = [
        threading.Thread(target=attempt, args=(agent,))
        for agent in (AgentName.CLAUDE, AgentName.CODEX)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert all(not thread.is_alive() for thread in threads)
    assert sum(task_id is not None for task_id in results.values()) == 1
    with factory() as session:
        reserved = TaskRepository(session).list(status=TaskStatus.RESERVED)
        assert len(reserved) == 1
        assert reserved[0].project_path == "C:/same"


@pytest.mark.parametrize("round_", range(10))
def test_concurrent_reservation_never_duplicates(tmp_path, config, round_):
    """Plan §14 Phase 2 test 4: the same any-task must go to exactly one agent."""
    engine = create_db_engine(tmp_path / f"race_{round_}.db")
    init_db(engine)
    factory = make_session_factory(engine)
    try:
        with factory() as session:
            set_available(session, AgentName.CLAUDE, AgentName.CODEX)
            task = TaskRepository(session).add(make_task(title="contested"))
            session.commit()
            task_id = task.id

        results: dict[AgentName, int | None] = {}
        barrier = threading.Barrier(2)

        def attempt(agent: AgentName) -> None:
            with factory() as session:
                barrier.wait()
                reserved = reserve_next_task(session, config, agent)
                results[agent] = None if reserved is None else reserved.id

        threads = [
            threading.Thread(target=attempt, args=(agent,))
            for agent in (AgentName.CLAUDE, AgentName.CODEX)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        winners = [agent for agent, got in results.items() if got == task_id]
        assert len(winners) == 1, f"duplicate assignment: {results}"

        with factory() as session:
            final = TaskRepository(session).get(task_id)
            assert final.status is TaskStatus.RESERVED
            assert final.assigned_agent is winners[0]
    finally:
        engine.dispose()
