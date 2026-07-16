from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from tests.fakes import FakeAdapter

from ai_queue_runner.application.agent_service import AgentService
from ai_queue_runner.application.runtime import QueueRuntime
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    TaskStatus,
)
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)


def test_twenty_task_queue_survives_runtime_restart_without_duplicates(tmp_path: Path):
    engine = create_db_engine(tmp_path / "soak.db")
    init_db(engine)
    factory = make_session_factory(engine)
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    config.app.logs_dir = tmp_path / "logs"
    config.app.global_auto_run = True
    tasks = TaskService(factory, config)
    agents = AgentService(factory)
    for agent in AgentName:
        agents.set_availability(agent, AgentStatus.AVAILABLE, None)

    task_ids = []
    for index in range(20):
        project = tmp_path / f"project-{index:02d}"
        project.mkdir()
        task_ids.append(
            tasks.create(
                TaskDraft(
                    title=f"soak-{index:02d}",
                    prompt="fake work",
                    project_path=project,
                    agent_policy=AgentPolicy.ANY,
                    approval_mode=ApprovalMode.AUTO,
                )
            ).id
        )

    adapters = {
        agent: FakeAdapter(
            name=agent,
            command=[sys.executable, "-c", "print('soak ok')"],
        )
        for agent in AgentName
    }
    runtime = QueueRuntime(factory, config, adapters)
    try:
        _process_until(runtime, tasks, task_ids, completed_target=10)
    finally:
        runtime.shutdown()

    runtime = QueueRuntime(factory, config, adapters)
    try:
        _process_until(runtime, tasks, task_ids, completed_target=20)
    finally:
        runtime.shutdown()

    views = [tasks.get(task_id) for task_id in task_ids]
    assert all(view is not None and view.status is TaskStatus.SUCCEEDED for view in views)
    runs = [run for task_id in task_ids for run in tasks.list_runs(task_id)]
    assert len(runs) == 20
    assert {run.task_id for run in runs} == set(task_ids)
    assert all(len(tasks.list_runs(task_id)) == 1 for task_id in task_ids)
    engine.dispose()


def _process_until(
    runtime: QueueRuntime,
    tasks: TaskService,
    task_ids: list[int],
    *,
    completed_target: int,
) -> None:
    deadline = time.monotonic() + 20
    while _succeeded_count(tasks, task_ids) < completed_target:
        runtime.tick_once()
        if time.monotonic() >= deadline:
            pytest.fail(f"soak queue did not reach {completed_target} completed tasks")
        time.sleep(0.01)
    while any(runtime.is_active(task_id) for task_id in task_ids):
        if time.monotonic() >= deadline:
            pytest.fail("active soak tasks did not finish before restart")
        time.sleep(0.01)


def _succeeded_count(tasks: TaskService, task_ids: list[int]) -> int:
    return sum(tasks.get(task_id).status is TaskStatus.SUCCEEDED for task_id in task_ids)
