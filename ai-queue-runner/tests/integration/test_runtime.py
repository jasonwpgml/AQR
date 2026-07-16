from __future__ import annotations

import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest
from tests.fakes import FakeAdapter

from ai_queue_runner.application.agent_service import AgentService
from ai_queue_runner.application.runtime import QueueRuntime
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    TaskStatus,
)
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.repositories import AgentStateRepository, TaskRepository


@pytest.fixture()
def runtime_app(tmp_path: Path):
    engine = create_db_engine(tmp_path / "runtime.db")
    init_db(engine)
    factory = make_session_factory(engine)
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    config.app.logs_dir = tmp_path / "logs"
    project = tmp_path / "project"
    project.mkdir()
    yield factory, config, project
    engine.dispose()


def task_draft(project: Path, **overrides) -> TaskDraft:
    return TaskDraft(
        title=overrides.pop("title", "runtime task"),
        prompt="do it",
        project_path=project,
        **overrides,
    )


def make_runtime(factory, config, adapters) -> QueueRuntime:
    return QueueRuntime(factory, config, adapters)


def test_tick_leaves_confirmation_task_reserved_until_rejected(runtime_app) -> None:
    factory, config, project = runtime_app
    tasks = TaskService(factory, config)
    agents = AgentService(factory)
    agents.set_availability(AgentName.CLAUDE, AgentStatus.AVAILABLE, None)
    task = tasks.create(task_draft(project, agent_policy=AgentPolicy.CLAUDE))
    runtime = make_runtime(factory, config, {AgentName.CLAUDE: FakeAdapter()})
    try:
        assert runtime.tick_once() == (task.id,)
        assert tasks.get(task.id).status is TaskStatus.RESERVED
        assert not runtime.is_active(task.id)

        assert runtime.reject(task.id)
        assert tasks.get(task.id).status is TaskStatus.WAITING
        claude = next(state for state in agents.list() if state.agent is AgentName.CLAUDE)
        assert claude.current_task_id is None
    finally:
        runtime.shutdown()


def test_approve_executes_reserved_task_on_background_loop(runtime_app) -> None:
    factory, config, project = runtime_app
    tasks = TaskService(factory, config)
    AgentService(factory).set_availability(
        AgentName.CLAUDE,
        AgentStatus.AVAILABLE,
        None,
    )
    task = tasks.create(task_draft(project, agent_policy=AgentPolicy.CLAUDE))
    adapter = FakeAdapter(command=[sys.executable, "-c", "print('approved')"])
    runtime = make_runtime(factory, config, {AgentName.CLAUDE: adapter})
    try:
        runtime.tick_once()
        assert runtime.approve(task.id)
        wait_until(lambda: tasks.get(task.id).status is TaskStatus.SUCCEEDED)
        assert not runtime.is_active(task.id)
        assert len(tasks.list_runs(task.id)) == 1
    finally:
        runtime.shutdown()


def test_auto_tick_runs_one_task_per_available_agent_concurrently(runtime_app) -> None:
    factory, config, project = runtime_app
    second_project = project.parent / "second-project"
    second_project.mkdir()
    config.app.global_auto_run = True
    tasks = TaskService(factory, config)
    agents = AgentService(factory)
    agents.set_availability(AgentName.CLAUDE, AgentStatus.AVAILABLE, None)
    agents.set_availability(AgentName.CODEX, AgentStatus.AVAILABLE, None)
    claude_task = tasks.create(
        task_draft(
            project,
            title="claude",
            agent_policy=AgentPolicy.CLAUDE,
            approval_mode=ApprovalMode.AUTO,
        )
    )
    codex_task = tasks.create(
        task_draft(
            second_project,
            title="codex",
            agent_policy=AgentPolicy.CODEX,
            approval_mode=ApprovalMode.AUTO,
        )
    )
    command = [sys.executable, "-c", "import time; time.sleep(0.3)"]
    runtime = make_runtime(
        factory,
        config,
        {
            AgentName.CLAUDE: FakeAdapter(name=AgentName.CLAUDE, command=command),
            AgentName.CODEX: FakeAdapter(name=AgentName.CODEX, command=command),
        },
    )
    try:
        assert set(runtime.tick_once()) == {claude_task.id, codex_task.id}
        wait_until(
            lambda: runtime.is_active(claude_task.id) and runtime.is_active(codex_task.id)
        )
        wait_until(
            lambda: tasks.get(claude_task.id).status is TaskStatus.SUCCEEDED
            and tasks.get(codex_task.id).status is TaskStatus.SUCCEEDED
        )
    finally:
        runtime.shutdown()


def test_tick_promotes_retry_and_due_agent_before_reserving(runtime_app) -> None:
    factory, config, project = runtime_app
    now = utcnow()
    with factory() as session:
        state = AgentStateRepository(session).get_or_create(AgentName.CLAUDE)
        state.status = AgentStatus.COOLDOWN
        state.available_at = now - timedelta(seconds=1)
        task = TaskRepository(session).add(
            Task(
                title="due retry",
                prompt="work",
                project_path=str(project),
                agent_policy=AgentPolicy.CLAUDE,
                status=TaskStatus.RETRY_WAIT,
                not_before=now - timedelta(seconds=1),
            )
        )
        session.commit()
        task_id = task.id

    runtime = make_runtime(factory, config, {AgentName.CLAUDE: FakeAdapter()})
    try:
        assert runtime.tick_once() == (task_id,)
        assert TaskService(factory, config).get(task_id).status is TaskStatus.RESERVED
    finally:
        runtime.shutdown()


def test_tick_keeps_future_cooldown_agent_unavailable(runtime_app) -> None:
    factory, config, project = runtime_app
    available_at = utcnow() + timedelta(minutes=5)
    with factory() as session:
        state = AgentStateRepository(session).get_or_create(AgentName.CLAUDE)
        state.status = AgentStatus.COOLDOWN
        state.available_at = available_at
        task = TaskRepository(session).add(
            Task(
                title="future cooldown",
                prompt="work",
                project_path=str(project),
                agent_policy=AgentPolicy.CLAUDE,
            )
        )
        session.commit()
        task_id = task.id

    runtime = make_runtime(factory, config, {AgentName.CLAUDE: FakeAdapter()})
    try:
        assert runtime.tick_once() == ()
        assert TaskService(factory, config).get(task_id).status is TaskStatus.WAITING
        state = next(
            state
            for state in AgentService(factory).list()
            if state.agent is AgentName.CLAUDE
        )
        assert state.status is AgentStatus.COOLDOWN
        assert state.available_at == available_at
    finally:
        runtime.shutdown()


def test_cancel_running_propagates_to_execution_service(runtime_app) -> None:
    factory, config, project = runtime_app
    tasks = TaskService(factory, config)
    AgentService(factory).set_availability(
        AgentName.CLAUDE,
        AgentStatus.AVAILABLE,
        None,
    )
    task = tasks.create(task_draft(project, agent_policy=AgentPolicy.CLAUDE))
    adapter = FakeAdapter(
        command=[sys.executable, "-c", "import time; print('started'); time.sleep(30)"]
    )
    runtime = make_runtime(factory, config, {AgentName.CLAUDE: adapter})
    try:
        runtime.tick_once()
        runtime.approve(task.id)
        wait_until(lambda: tasks.get(task.id).status is TaskStatus.RUNNING)
        assert runtime.cancel_running(task.id)
        wait_until(lambda: tasks.get(task.id).status is TaskStatus.FAILED)
        assert tasks.get(task.id).last_error == "cancelled by user"
    finally:
        runtime.shutdown()


def test_scheduler_lifecycle_properties(runtime_app) -> None:
    factory, config, _project = runtime_app
    scheduler = FakeScheduler()
    runtime = QueueRuntime(factory, config, {}, scheduler=scheduler)

    runtime.start()
    assert runtime.running
    assert not runtime.paused
    assert scheduler.job[2]["seconds"] == config.scheduler.poll_interval_seconds

    runtime.pause()
    assert runtime.paused
    assert not runtime.running
    runtime.start()
    assert runtime.running

    runtime.shutdown()
    assert not runtime.running
    assert scheduler.shutdown_wait is True


def test_reserve_now_selects_requested_lower_priority_task(runtime_app) -> None:
    factory, config, project = runtime_app
    second_project = project.parent / "second-project"
    second_project.mkdir()
    tasks = TaskService(factory, config)
    AgentService(factory).set_availability(
        AgentName.CLAUDE,
        AgentStatus.AVAILABLE,
        None,
    )
    high = tasks.create(
        task_draft(project, title="high", priority=100, agent_policy=AgentPolicy.CLAUDE)
    )
    selected = tasks.create(
        task_draft(
            second_project,
            title="selected",
            priority=1,
            agent_policy=AgentPolicy.CLAUDE,
        )
    )
    runtime = make_runtime(factory, config, {AgentName.CLAUDE: FakeAdapter()})
    try:
        assert runtime.reserve_now(selected.id)
        assert tasks.get(selected.id).status is TaskStatus.RESERVED
        assert tasks.get(high.id).status is TaskStatus.WAITING
        assert not runtime.is_active(selected.id)
    finally:
        runtime.shutdown()


class FakeScheduler:
    def __init__(self) -> None:
        self.job = None
        self.started = False
        self.is_paused = False
        self.shutdown_wait = None

    def add_job(self, function, trigger, **kwargs) -> None:
        self.job = (function, trigger, kwargs)

    def start(self) -> None:
        self.started = True

    def pause(self) -> None:
        self.is_paused = True

    def resume(self) -> None:
        self.is_paused = False

    def shutdown(self, *, wait: bool) -> None:
        self.shutdown_wait = wait
        self.started = False


def wait_until(predicate, *, timeout_seconds: float = 5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("condition was not met before the deadline")
        time.sleep(0.01)
