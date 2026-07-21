import subprocess
import sys
from datetime import timedelta

import pytest

from ai_queue_runner.application.recovery_service import (
    OrphanedRun,
    find_orphaned_running_tasks,
    recover_orphaned_task,
    release_stale_reservations,
)
from ai_queue_runner.application.scheduler_service import reserve_next_task
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.process_runner import get_process_started_at
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


def make_running(session, config, *, retry_count=0) -> Task:
    task = reserve_one(session, config)
    TaskRepository(session).set_status(task, TaskStatus.RUNNING)
    task.retry_count = retry_count
    task.started_at = utcnow()
    state = AgentStateRepository(session).get(AgentName.CLAUDE)
    assert state is not None
    state.status = AgentStatus.BUSY
    session.commit()
    return task


def add_run(
    session,
    task: Task,
    *,
    pid: int | None,
    started_at=None,
) -> TaskRun:
    run = TaskRun(
        task_id=task.id,
        agent=AgentName.CLAUDE,
        attempt=task.retry_count + 1,
        command=[sys.executable, "-c", "pass"],
        pid=pid,
        **({"started_at": started_at} if started_at is not None else {}),
    )
    session.add(run)
    session.commit()
    return run


def exited_python_pid() -> int:
    completed = subprocess.run(
        [sys.executable, "-c", "import os; print(os.getpid()); pass"],
        check=True,
        capture_output=True,
        text=True,
    )
    return int(completed.stdout)


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


def test_find_orphaned_running_task_reports_dead_pid(session, config):
    task = make_running(session, config)
    run = add_run(session, task, pid=exited_python_pid())

    assert find_orphaned_running_tasks(session) == [
        OrphanedRun(task_id=task.id, run_id=run.id, pid=run.pid, pid_alive=False)
    ]


def test_recover_orphaned_task_to_waiting(session, config):
    task = make_running(session, config, retry_count=1)
    run = add_run(session, task, pid=exited_python_pid())

    recover_orphaned_task(session, task, to_status=TaskStatus.WAITING)

    assert task.status is TaskStatus.WAITING
    assert task.assigned_agent is None
    assert task.reserved_at is None
    assert task.started_at is None
    assert task.retry_count == 1
    assert task.last_error == "recovered after crash"
    assert run.finished_at is not None
    assert run.error_type is ErrorType.CRASH
    assert run.exit_code is None
    state = AgentStateRepository(session).get(AgentName.CLAUDE)
    assert state is not None
    assert state.status is AgentStatus.UNKNOWN
    assert state.current_task_id is None


def test_recover_orphaned_task_to_failed(session, config):
    task = make_running(session, config)
    run = add_run(session, task, pid=exited_python_pid())

    recover_orphaned_task(session, task, to_status=TaskStatus.FAILED)

    assert task.status is TaskStatus.FAILED
    assert task.finished_at is not None
    assert task.last_error == "recovered after crash"
    assert run.finished_at is not None
    assert run.error_type is ErrorType.CRASH


def test_alive_pid_requires_explicit_kill_before_recovery(session, config):
    task = make_running(session, config)
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        add_run(
            session,
            task,
            pid=child.pid,
            started_at=get_process_started_at(child.pid),
        )
        [orphaned] = find_orphaned_running_tasks(session)
        assert orphaned.pid_alive is True
        assert orphaned.pid_identity_verified is True

        with pytest.raises(RuntimeError, match="is still alive"):
            recover_orphaned_task(session, task, to_status=TaskStatus.FAILED)

        recover_orphaned_task(
            session,
            task,
            to_status=TaskStatus.FAILED,
            kill_if_alive=True,
        )
        child.wait(timeout=10)
        assert child.poll() is not None
        assert task.status is TaskStatus.FAILED
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=10)


def test_recovery_never_kills_a_reused_pid(session, config):
    task = make_running(session, config)
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        add_run(
            session,
            task,
            pid=unrelated.pid,
            started_at=utcnow() - timedelta(days=1),
        )

        [orphaned] = find_orphaned_running_tasks(session)
        assert orphaned.pid_alive is False

        recover_orphaned_task(
            session,
            task,
            to_status=TaskStatus.FAILED,
            kill_if_alive=True,
        )

        assert unrelated.poll() is None
        assert task.status is TaskStatus.FAILED
    finally:
        unrelated.kill()
        unrelated.wait(timeout=10)


def test_recovery_refuses_unverifiable_live_process(
    session,
    config,
    monkeypatch,
):
    task = make_running(session, config)
    add_run(session, task, pid=12345, started_at=utcnow())
    monkeypatch.setattr(
        "ai_queue_runner.application.recovery_service.psutil.pid_exists",
        lambda _pid: True,
    )
    monkeypatch.setattr(
        "ai_queue_runner.application.recovery_service.get_process_started_at",
        lambda _pid: None,
    )

    [orphaned] = find_orphaned_running_tasks(session)
    assert orphaned.pid_alive is True
    assert orphaned.pid_identity_verified is False

    with pytest.raises(RuntimeError, match="could not be verified"):
        recover_orphaned_task(
            session,
            task,
            to_status=TaskStatus.FAILED,
            kill_if_alive=True,
        )
    assert task.status is TaskStatus.RUNNING


def test_recover_orphaned_task_rejects_invalid_status(session, config):
    task = make_running(session, config)

    with pytest.raises(ValueError, match="failed or waiting"):
        recover_orphaned_task(session, task, to_status=TaskStatus.SUCCEEDED)


def test_find_orphaned_running_tasks_ignores_other_statuses(session):
    TaskRepository(session).add(
        Task(title="t", prompt="p", project_path="C:/projects/demo", agent_policy="any")
    )
    session.commit()

    assert find_orphaned_running_tasks(session) == []


def test_running_task_without_run_is_reported_and_recoverable(session, config):
    task = make_running(session, config)

    assert find_orphaned_running_tasks(session) == [
        OrphanedRun(task_id=task.id, run_id=None, pid=None, pid_alive=False)
    ]

    recover_orphaned_task(session, task, to_status=TaskStatus.WAITING)

    assert task.status is TaskStatus.WAITING
