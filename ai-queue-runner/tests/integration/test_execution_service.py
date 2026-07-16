from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from tests.fakes import FakeAdapter

from ai_queue_runner.application.execution_service import ExecutionService
from ai_queue_runner.application.recovery_service import promote_retry_tasks
from ai_queue_runner.application.scheduler_service import reserve_next_task
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.process_runner import ProcessRunner
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    TaskRepository,
    TaskRunRepository,
)


@pytest.fixture()
def config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.app.logs_dir = tmp_path / "logs"
    config.app.allowed_project_roots = [tmp_path]
    return config


@pytest.fixture()
def session(tmp_path: Path):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    with make_session_factory(engine)() as session:
        yield session
    engine.dispose()


@pytest.fixture()
def project_path(tmp_path: Path) -> Path:
    path = tmp_path / "project"
    path.mkdir()
    return path


def python_adapter(script: str, *, agent: AgentName = AgentName.CLAUDE) -> FakeAdapter:
    return FakeAdapter(name=agent, command=[sys.executable, "-c", script])


def reserve_task(
    session,
    config: AppConfig,
    project_path: Path,
    adapter: FakeAdapter,
    **overrides,
) -> Task:
    state = AgentStateRepository(session).get_or_create(adapter.name)
    state.status = AgentStatus.AVAILABLE
    values = {
        "title": "execute me",
        "prompt": "do the work",
        "project_path": str(project_path),
        "agent_policy": adapter.name.value,
        **overrides,
    }
    TaskRepository(session).add(Task(**values))
    session.commit()
    task = reserve_next_task(session, config, adapter.name)
    assert task is not None
    return task


async def execute_reserved(
    session,
    config: AppConfig,
    project_path: Path,
    adapter: FakeAdapter,
    **overrides,
):
    task = reserve_task(session, config, project_path, adapter, **overrides)
    run = await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)
    return task, run


async def test_success_persists_run_logs_and_releases_agent(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter(
        "import sys; print('stdout result'); print('stderr result', file=sys.stderr)"
    )

    task, run = await execute_reserved(session, config, project_path, adapter)

    assert task.status is TaskStatus.SUCCEEDED
    assert task.finished_at is not None
    assert run.pid is not None
    assert run.exit_code == 0
    assert Path(run.stdout_path).read_text(encoding="utf-8") == "stdout result\n"
    assert Path(run.stderr_path).read_text(encoding="utf-8") == "stderr result\n"
    state = AgentStateRepository(session).get(adapter.name)
    assert state.status is AgentStatus.AVAILABLE
    assert state.current_task_id is None


async def test_success_stores_summary_from_optional_adapter_hook(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("print('summary source')")
    adapter.summarize_output = lambda output: f"summary: {output.strip()}"

    _task, run = await execute_reserved(session, config, project_path, adapter)

    assert run.result_summary == "summary: summary source"


async def test_success_clears_previous_task_and_agent_errors(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("print('recovered')")
    task = reserve_task(session, config, project_path, adapter, retry_count=1)
    task.last_error = "previous failure"
    state = AgentStateRepository(session).get(adapter.name)
    state.last_error = "previous agent failure"
    session.commit()

    await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)

    assert task.status is TaskStatus.SUCCEEDED
    assert task.last_error is None
    assert state.status is AgentStatus.AVAILABLE
    assert state.last_error is None


async def test_failure_with_retries_remaining_enters_retry_wait(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("import sys; print('ordinary failure', file=sys.stderr); sys.exit(7)")
    retry_delay = 3
    earliest = utcnow() + timedelta(minutes=retry_delay)

    task, run = await execute_reserved(
        session,
        config,
        project_path,
        adapter,
        max_retries=2,
        retry_delay_minutes=retry_delay,
    )
    latest = utcnow() + timedelta(minutes=retry_delay)

    assert task.status is TaskStatus.RETRY_WAIT
    assert task.retry_count == 1
    assert earliest <= task.not_before <= latest
    assert "ordinary failure" in task.last_error
    assert run.error_type is ErrorType.UNKNOWN


async def test_failure_without_retries_enters_failed(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("import sys; sys.exit(9)")

    task, run = await execute_reserved(
        session,
        config,
        project_path,
        adapter,
        max_retries=0,
    )

    assert task.status is TaskStatus.FAILED
    assert task.retry_count == 1
    assert task.finished_at is not None
    assert run.exit_code == 9
    assert run.error_type is ErrorType.UNKNOWN


async def test_structured_error_marker_overrides_zero_exit(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("print('authentication failed')")

    task, run = await execute_reserved(
        session,
        config,
        project_path,
        adapter,
        max_retries=0,
    )

    assert task.status is TaskStatus.BLOCKED
    assert task.retry_count == 0
    assert run.exit_code == 0
    assert run.error_type is ErrorType.AUTH
    agent_state = AgentStateRepository(session).get(AgentName.CLAUDE)
    assert agent_state is not None
    assert agent_state.status is AgentStatus.ERROR
    assert "authentication failed" in agent_state.last_error


async def test_usage_limit_returns_task_to_queue_and_cools_down_agent(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter(
        "import sys; print('USAGE LIMIT reached', file=sys.stderr); sys.exit(1)"
    )

    task, run = await execute_reserved(session, config, project_path, adapter)

    assert task.status is TaskStatus.WAITING
    assert task.retry_count == 0
    assert task.assigned_agent is None
    assert task.reserved_at is None
    assert task.started_at is None
    assert run.error_type is ErrorType.USAGE_LIMIT
    state = AgentStateRepository(session).get(adapter.name)
    assert state.status is AgentStatus.COOLDOWN
    assert state.available_at is None
    assert state.current_task_id is None


async def test_success_output_discussing_rate_limits_remains_successful(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("print('write documentation about rate limit handling')")

    task, run = await execute_reserved(session, config, project_path, adapter)

    assert run.exit_code == 0
    assert task.status is TaskStatus.SUCCEEDED


async def test_timeout_uses_retry_path(
    session,
    config: AppConfig,
    project_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = python_adapter("import time; time.sleep(30)")
    task = reserve_task(session, config, project_path, adapter)
    service = ExecutionService(config, ProcessRunner())
    monkeypatch.setattr(service, "_timeout_seconds", lambda _adapter: 0.1)
    loop = asyncio.get_running_loop()
    started = loop.time()

    run = await service.execute(session, task, adapter)

    assert loop.time() - started < 5
    assert task.status is TaskStatus.RETRY_WAIT
    assert task.retry_count == 1
    assert run.error_type is ErrorType.TIMEOUT
    assert run.pid is not None


async def test_spawn_failure_is_recorded_as_crash(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = FakeAdapter(command=["definitely-not-a-real-binary-xyz"])

    task, run = await execute_reserved(
        session,
        config,
        project_path,
        adapter,
        max_retries=0,
    )

    assert task.status is TaskStatus.FAILED
    assert task.retry_count == 1
    assert run.error_type is ErrorType.CRASH
    assert run.pid is None
    assert run.exit_code is None
    assert run.finished_at is not None


async def test_cancellation_is_persisted_then_propagated(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter(
        "import time; print('started', flush=True); time.sleep(30)"
    )
    task = reserve_task(session, config, project_path, adapter)
    execution = asyncio.create_task(
        ExecutionService(config, ProcessRunner()).execute(session, task, adapter)
    )
    stdout_path = config.app.logs_dir / "tasks" / str(task.id) / "attempt_1.stdout.log"
    await wait_until(
        lambda: stdout_path.exists()
        and "started" in stdout_path.read_text(encoding="utf-8"),
    )

    active_run = TaskRunRepository(session).list_for_task(task.id)[0]
    assert active_run.pid is not None
    execution.cancel()
    with pytest.raises(asyncio.CancelledError):
        await execution

    run = TaskRunRepository(session).list_for_task(task.id)[0]
    assert task.status is TaskStatus.FAILED
    assert task.last_error == "cancelled by user"
    assert run.error_type is ErrorType.CANCELLED
    assert run.finished_at is not None
    state = AgentStateRepository(session).get(adapter.name)
    assert state.status is AgentStatus.AVAILABLE
    assert state.current_task_id is None


def test_promote_retry_tasks_only_promotes_due_work(
    session,
    project_path: Path,
) -> None:
    now = utcnow()
    repo = TaskRepository(session)
    due = repo.add(
        Task(
            title="due",
            prompt="p",
            project_path=str(project_path),
            agent_policy="any",
            status=TaskStatus.RETRY_WAIT,
            not_before=now - timedelta(seconds=1),
        )
    )
    future = repo.add(
        Task(
            title="future",
            prompt="p",
            project_path=str(project_path),
            agent_policy="any",
            status=TaskStatus.RETRY_WAIT,
            not_before=now + timedelta(minutes=1),
        )
    )
    session.commit()

    promoted = promote_retry_tasks(session, now)

    assert [task.id for task in promoted] == [due.id]
    assert due.status is TaskStatus.WAITING
    assert future.status is TaskStatus.RETRY_WAIT


async def test_full_pipeline_releases_agent_for_next_task(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("print('done')")
    state = AgentStateRepository(session).get_or_create(adapter.name)
    state.status = AgentStatus.AVAILABLE
    repo = TaskRepository(session)
    first = repo.add(
        Task(
            title="first",
            prompt="p",
            project_path=str(project_path),
            agent_policy=adapter.name.value,
            priority=100,
        )
    )
    second = repo.add(
        Task(
            title="second",
            prompt="p",
            project_path=str(project_path),
            agent_policy=adapter.name.value,
            priority=50,
        )
    )
    session.commit()

    reserved = reserve_next_task(session, config, adapter.name)
    assert reserved.id == first.id
    await ExecutionService(config, ProcessRunner()).execute(session, reserved, adapter)
    next_task = reserve_next_task(session, config, adapter.name)

    assert first.status is TaskStatus.SUCCEEDED
    assert next_task is not None and next_task.id == second.id
    assert next_task.status is TaskStatus.RESERVED


async def test_execute_rejects_waiting_task(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    task = TaskRepository(session).add(
        Task(
            title="not reserved",
            prompt="p",
            project_path=str(project_path),
            agent_policy="claude",
        )
    )
    session.commit()

    with pytest.raises(ValueError, match="must be reserved"):
        await ExecutionService(config, ProcessRunner()).execute(
            session,
            task,
            python_adapter("print('must not run')"),
        )


async def test_invalid_project_path_is_blocked_before_spawn(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("raise SystemExit('must not spawn')")
    task = reserve_task(session, config, project_path, adapter)
    config.app.allowed_project_roots = []

    with pytest.raises(ValueError, match="allowed project root"):
        await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)

    assert task.status is TaskStatus.BLOCKED
    assert task.assigned_agent is None
    assert "allowed project root" in task.last_error
    assert TaskRunRepository(session).list_for_task(task.id) == []


async def wait_until(predicate, timeout_seconds: float = 5) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while not predicate():
        if loop.time() >= deadline:
            pytest.fail("condition was not met before the deadline")
        await asyncio.sleep(0.01)
