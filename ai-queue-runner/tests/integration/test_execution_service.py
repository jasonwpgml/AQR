from __future__ import annotations

import asyncio
import sys
from datetime import UTC, timedelta
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
from ai_queue_runner.infrastructure.adapters.claude import ClaudeAdapter
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


async def test_any_failure_prefers_other_agent_on_next_retry(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("import sys; sys.exit(7)")
    task = reserve_task(
        session,
        config,
        project_path,
        adapter,
        agent_policy="any",
        max_retries=2,
        retry_delay_minutes=0,
    )
    AgentStateRepository(session).get_or_create(AgentName.CODEX).status = (
        AgentStatus.AVAILABLE
    )
    session.commit()

    run = await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)
    promote_retry_tasks(session)

    assert task.retry_count == 1
    assert task.fallback_agent is AgentName.CODEX
    assert run.fallback_to_agent is AgentName.CODEX
    assert "claude -> codex" in task.last_error
    assert reserve_next_task(session, config, AgentName.CLAUDE) is None
    switched = reserve_next_task(session, config, AgentName.CODEX)
    assert switched is not None and switched.id == task.id
    assert switched.assigned_agent is AgentName.CODEX


async def test_any_consecutive_failure_threshold_switches_agent(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    config.scheduler.fallback_failure_threshold = 2
    adapter = python_adapter("import sys; sys.exit(8)")
    task = reserve_task(
        session,
        config,
        project_path,
        adapter,
        agent_policy="any",
        max_retries=3,
        retry_delay_minutes=0,
    )
    AgentStateRepository(session).get_or_create(AgentName.CODEX).status = (
        AgentStatus.AVAILABLE
    )
    session.commit()

    first_run = await ExecutionService(config, ProcessRunner()).execute(
        session, task, adapter
    )
    promote_retry_tasks(session)

    assert first_run.fallback_to_agent is None
    assert task.fallback_agent is None
    same_agent_retry = reserve_next_task(session, config, AgentName.CLAUDE)
    assert same_agent_retry is not None and same_agent_retry.id == task.id

    second_run = await ExecutionService(config, ProcessRunner()).execute(
        session, same_agent_retry, adapter
    )
    promote_retry_tasks(session)

    assert task.retry_count == 2
    assert task.fallback_agent is AgentName.CODEX
    assert second_run.fallback_to_agent is AgentName.CODEX
    assert reserve_next_task(session, config, AgentName.CLAUDE) is None
    switched = reserve_next_task(session, config, AgentName.CODEX)
    assert switched is not None and switched.id == task.id


async def test_dedicated_failure_never_switches_agent(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter("import sys; sys.exit(9)")
    task = reserve_task(
        session,
        config,
        project_path,
        adapter,
        max_retries=2,
        retry_delay_minutes=0,
    )
    AgentStateRepository(session).get_or_create(AgentName.CODEX).status = (
        AgentStatus.AVAILABLE
    )
    session.commit()

    run = await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)
    promote_retry_tasks(session)

    assert task.fallback_agent is None
    assert run.fallback_to_agent is None
    assert reserve_next_task(session, config, AgentName.CODEX) is None
    retried = reserve_next_task(session, config, AgentName.CLAUDE)
    assert retried is not None and retried.id == task.id


async def test_fallback_off_preserves_original_any_retry_selection(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    config.scheduler.fallback_enabled = False
    adapter = python_adapter("import sys; sys.exit(10)")
    task = reserve_task(
        session,
        config,
        project_path,
        adapter,
        agent_policy="any",
        max_retries=2,
        retry_delay_minutes=0,
    )
    AgentStateRepository(session).get_or_create(AgentName.CODEX).status = (
        AgentStatus.AVAILABLE
    )
    session.commit()

    run = await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)
    promote_retry_tasks(session)

    assert task.fallback_agent is None
    assert run.fallback_to_agent is None
    retried = reserve_next_task(session, config, AgentName.CLAUDE)
    assert retried is not None and retried.id == task.id


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
    config.agents[AgentName.CLAUDE.value].default_cooldown_minutes = 17
    adapter = python_adapter(
        "import sys; print('USAGE LIMIT reached', file=sys.stderr); sys.exit(1)"
    )
    earliest = utcnow() + timedelta(minutes=17)

    task, run = await execute_reserved(session, config, project_path, adapter)
    latest = utcnow() + timedelta(minutes=17)

    assert task.status is TaskStatus.WAITING
    assert task.retry_count == 0
    assert task.assigned_agent is None
    assert task.reserved_at is None
    assert task.started_at is None
    assert run.error_type is ErrorType.USAGE_LIMIT
    state = AgentStateRepository(session).get(adapter.name)
    assert state.status is AgentStatus.COOLDOWN
    assert earliest <= state.available_at <= latest
    assert state.current_task_id is None
    assert state.available_at.isoformat(sep=" ", timespec="seconds") in task.last_error


async def test_any_usage_limit_switches_immediately_without_consuming_retry(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    adapter = python_adapter(
        "import sys; print('USAGE LIMIT reached', file=sys.stderr); sys.exit(1)"
    )
    task = reserve_task(
        session,
        config,
        project_path,
        adapter,
        agent_policy="any",
    )
    AgentStateRepository(session).get_or_create(AgentName.CODEX).status = (
        AgentStatus.AVAILABLE
    )
    session.commit()

    run = await ExecutionService(config, ProcessRunner()).execute(session, task, adapter)

    assert task.status is TaskStatus.WAITING
    assert task.retry_count == 0
    assert task.fallback_agent is AgentName.CODEX
    assert run.fallback_to_agent is AgentName.CODEX
    switched = reserve_next_task(session, config, AgentName.CODEX)
    assert switched is not None and switched.id == task.id
    assert switched.assigned_agent is AgentName.CODEX


async def test_usage_limit_uses_exact_reset_extracted_from_output(
    session,
    config: AppConfig,
    project_path: Path,
) -> None:
    reset_at = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    reset_timestamp = int(reset_at.replace(tzinfo=UTC).timestamp())
    event = (
        '{"type":"rate_limit_event","rate_limit_info":'
        f'{{"status":"rejected","resetsAt":{reset_timestamp}}}}}'
    )
    adapter = python_adapter(f"import sys; print({event!r}); sys.exit(1)")
    adapter.extract_limit_reset = ClaudeAdapter().extract_limit_reset

    task, run = await execute_reserved(session, config, project_path, adapter)

    state = AgentStateRepository(session).get(adapter.name)
    assert run.error_type is ErrorType.USAGE_LIMIT
    assert task.status is TaskStatus.WAITING
    assert state.status is AgentStatus.COOLDOWN
    assert state.available_at == reset_at
    assert reset_at.isoformat(sep=" ", timespec="seconds") in task.last_error


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
