from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_queue_runner.application.agent_service import AgentService
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import TaskRun
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.path_policy import PathPolicyError
from ai_queue_runner.infrastructure.repositories import TaskRepository, TaskRunRepository


@pytest.fixture()
def application(tmp_path: Path):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    factory = make_session_factory(engine)
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    project = tmp_path / "project"
    project.mkdir()
    yield TaskService(factory, config), AgentService(factory), factory, project
    engine.dispose()


def draft(project: Path, **overrides) -> TaskDraft:
    values = {
        "title": "task",
        "prompt": "do work",
        "project_path": project,
        **overrides,
    }
    return TaskDraft(**values)


def test_task_draft_rejects_unknown_fields_and_bad_priority(project_path: Path) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        TaskDraft(
            title="task",
            prompt="work",
            project_path=project_path,
            speculative=True,
        )
    with pytest.raises(ValidationError, match="less than or equal to 100"):
        draft(project_path, priority=101)
    with pytest.raises(ValidationError, match="reserved for internal use"):
        draft(project_path, tags=["__ai_queue_runner_archived__"])


@pytest.fixture()
def project_path(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    return project


def test_task_crud_returns_detached_immutable_dtos_and_dependencies(application) -> None:
    tasks, _agents, _factory, project = application
    prerequisite = tasks.create(draft(project, title="prerequisite"))
    created = tasks.create(
        draft(
            project,
            title="dependent",
            priority=80,
            dependency_ids=[prerequisite.id],
            tags=["ui", "phase-5"],
        )
    )

    assert tasks.list() == [prerequisite, created]
    assert tasks.get(created.id) == created
    assert created.project_path == str(project.resolve())
    assert created.dependency_ids == (prerequisite.id,)
    assert created.tags == ("ui", "phase-5")
    with pytest.raises(FrozenInstanceError):
        created.title = "mutated"  # type: ignore[misc]

    updated = tasks.update(
        created.id,
        draft(project, title="updated", priority=95, dependency_ids=[prerequisite.id]),
    )
    assert updated.title == "updated"
    assert updated.priority == 95


def test_create_batch_persists_estimates_in_one_transaction(
    application, monkeypatch
) -> None:
    tasks, _agents, _factory, project = application
    created = tasks.create_batch(
        [
            draft(project, title="first", estimated_tokens=1_200),
            draft(project, title="second", estimated_tokens=800),
        ]
    )

    assert [task.estimated_tokens for task in created] == [1_200, 800]
    assert [task.title for task in tasks.list()] == ["first", "second"]

    original_add = TaskRepository.add
    calls = 0

    def fail_on_second_add(repo, task):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("simulated second insert failure")
        return original_add(repo, task)

    monkeypatch.setattr(TaskRepository, "add", fail_on_second_add)
    with pytest.raises(RuntimeError, match="second insert failure"):
        tasks.create_batch(
            [
                draft(project, title="rolled back first", estimated_tokens=500),
                draft(project, title="rolled back second", estimated_tokens=500),
            ]
        )

    assert [task.title for task in tasks.list()] == ["first", "second"]


def test_activity_between_returns_tasks_with_events_inside_range(application) -> None:
    tasks, _agents, factory, project = application
    created_event = tasks.create(draft(project, title="created in range"))
    finished_event = tasks.create(draft(project, title="finished in range"))
    scheduled_event = tasks.create(draft(project, title="scheduled in range"))
    outside = tasks.create(draft(project, title="outside range"))
    start = datetime(2030, 1, 10)
    end = datetime(2030, 1, 11)

    with factory() as session:
        repo = TaskRepository(session)
        for task_id in (created_event.id, finished_event.id, scheduled_event.id, outside.id):
            repo.get(task_id).created_at = datetime(2030, 1, 1)
        repo.get(created_event.id).created_at = start + timedelta(hours=1)
        repo.get(finished_event.id).finished_at = start + timedelta(hours=2)
        repo.get(scheduled_event.id).not_before = start + timedelta(hours=3)
        repo.get(outside.id).not_before = end
        session.commit()

    activities = tasks.activity_between(start, end)

    assert [activity.id for activity in activities] == [
        created_event.id,
        finished_event.id,
        scheduled_event.id,
    ]
    assert activities[0].created_at == start + timedelta(hours=1)
    assert activities[1].finished_at == start + timedelta(hours=2)
    assert activities[2].not_before == start + timedelta(hours=3)

    with pytest.raises(ValueError, match="naive UTC"):
        tasks.activity_between(start.astimezone(), end)
    with pytest.raises(ValueError, match="before end"):
        tasks.activity_between(end, start)


def test_create_and_update_enforce_path_policy_and_waiting_only(application, tmp_path) -> None:
    tasks, _agents, factory, project = application
    outside = tmp_path.parent / f"outside-{tmp_path.name}"
    outside.mkdir(exist_ok=True)
    with pytest.raises(PathPolicyError, match="outside allowed roots"):
        tasks.create(draft(outside))

    created = tasks.create(draft(project))
    with factory() as session:
        task = TaskRepository(session).get(created.id)
        TaskRepository(session).set_status(task, TaskStatus.RESERVED)
        session.commit()
    with pytest.raises(ValueError, match="only waiting"):
        tasks.update(created.id, draft(project, title="too late"))


def test_duplicate_cancel_retry_and_run_history(application) -> None:
    tasks, _agents, factory, project = application
    source = tasks.create(draft(project, title="source", priority=77))
    copy = tasks.duplicate(source.id)
    assert copy.id != source.id
    assert copy.title == source.title
    assert copy.status is TaskStatus.WAITING

    cancelled = tasks.cancel(copy.id)
    assert cancelled.status is TaskStatus.CANCELLED

    with factory() as session:
        repo = TaskRepository(session)
        task = repo.get(source.id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.FAILED)
        task.retry_count = 3
        task.last_error = "failed"
        TaskRunRepository(session).add(
            TaskRun(
                task_id=task.id,
                agent=AgentName.CLAUDE,
                attempt=3,
                command=["claude", "-p"],
                exit_code=1,
                error_type=ErrorType.UNKNOWN,
            )
        )
        session.commit()

    retried = tasks.retry(source.id)
    assert retried.status is TaskStatus.WAITING
    assert retried.retry_count == 0
    assert retried.last_error is None
    runs = tasks.list_runs(source.id)
    assert len(runs) == 1
    assert runs[0].command == ("claude", "-p")
    assert runs[0].error_type is ErrorType.UNKNOWN


def test_dependency_cycles_and_missing_tasks_are_rejected(application) -> None:
    tasks, _agents, _factory, project = application
    first = tasks.create(draft(project, title="first"))
    second = tasks.create(draft(project, title="second", dependency_ids=[first.id]))

    with pytest.raises(ValueError, match="cycle"):
        tasks.update(first.id, draft(project, dependency_ids=[second.id]))
    with pytest.raises(ValueError, match="not found"):
        tasks.create(draft(project, dependency_ids=[9999]))
    with pytest.raises(KeyError, match="not found"):
        tasks.get(9999) or tasks.list_runs(9999)


def test_blocked_task_can_be_returned_to_waiting(application) -> None:
    tasks, _agents, factory, project = application
    created = tasks.create(draft(project, title="blocked"))
    with factory() as session:
        repo = TaskRepository(session)
        task = repo.get(created.id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.BLOCKED)
        task.assigned_agent = AgentName.CLAUDE
        task.last_error = "path was temporarily unavailable"
        session.commit()

    retried = tasks.retry(created.id)

    assert retried.status is TaskStatus.WAITING
    assert retried.assigned_agent is None
    assert retried.last_error is None


def test_move_to_top_and_archive_completed_task(application) -> None:
    tasks, _agents, factory, project = application
    first = tasks.create(draft(project, title="first", priority=100))
    second = tasks.create(draft(project, title="second", priority=20))

    promoted = tasks.move_to_top(second.id)

    assert promoted.priority == 100
    assert tasks.get(first.id).priority == 99

    with factory() as session:
        repo = TaskRepository(session)
        task = repo.get(second.id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        session.commit()

    archived = tasks.archive(second.id)

    assert archived.archived is True
    assert "__ai_queue_runner_archived__" not in archived.tags
    assert all(task.id != second.id for task in tasks.list())
    assert [task.id for task in tasks.list(status="archived")] == [second.id]

    duplicate = tasks.duplicate(second.id)
    assert duplicate.archived is False
    assert duplicate.status is TaskStatus.WAITING


def test_delete_terminal_task_removes_its_run_history(application) -> None:
    tasks, _agents, factory, project = application
    created = tasks.create(draft(project, title="completed"))
    with factory() as session:
        repo = TaskRepository(session)
        task = repo.get(created.id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        TaskRunRepository(session).add(
            TaskRun(
                task_id=task.id,
                agent=AgentName.CODEX,
                attempt=1,
                command=["codex", "exec"],
                exit_code=0,
            )
        )
        session.commit()

    tasks.delete(created.id)

    assert tasks.get(created.id) is None
    with factory() as session:
        assert TaskRunRepository(session).list_for_task(created.id) == []


@pytest.mark.parametrize("status", [TaskStatus.RESERVED, TaskStatus.RUNNING])
def test_delete_rejects_reserved_and_running_tasks(application, status) -> None:
    tasks, _agents, factory, project = application
    created = tasks.create(draft(project, title=status.value))
    with factory() as session:
        repo = TaskRepository(session)
        task = repo.get(created.id)
        repo.set_status(task, TaskStatus.RESERVED)
        if status is TaskStatus.RUNNING:
            repo.set_status(task, TaskStatus.RUNNING)
        session.commit()

    with pytest.raises(ValueError, match="must be cancelled before deletion"):
        tasks.delete(created.id)


def test_delete_lists_dependents_and_allows_removal_after_they_are_deleted(application) -> None:
    tasks, _agents, _factory, project = application
    prerequisite = tasks.create(draft(project, title="prerequisite"))
    dependent = tasks.create(
        draft(project, title="dependent", dependency_ids=[prerequisite.id])
    )

    with pytest.raises(ValueError, match=rf"dependent tasks: {dependent.id}"):
        tasks.delete(prerequisite.id)

    tasks.delete(dependent.id)
    tasks.delete(prerequisite.id)

    assert tasks.list() == []


def test_agent_manual_availability_and_due_promotion(application) -> None:
    _tasks, agents, _factory, _project = application
    now = utcnow()
    future = now + timedelta(minutes=5)

    initial = agents.list()
    assert [state.agent for state in initial] == list(AgentName)
    assert all(state.status is AgentStatus.UNKNOWN for state in initial)

    cooldown = agents.set_availability(
        AgentName.CLAUDE,
        AgentStatus.COOLDOWN,
        future,
        now=now,
    )
    available = agents.set_manual_availability(AgentName.CODEX, None, now=now)
    assert cooldown.status is AgentStatus.COOLDOWN
    assert cooldown.available_at == future
    assert available.status is AgentStatus.AVAILABLE

    assert agents.promote_due_cooldowns(now + timedelta(minutes=4)) == []
    promoted = agents.promote_due_cooldowns(now + timedelta(minutes=6))
    assert [state.agent for state in promoted] == [AgentName.CLAUDE]
    assert promoted[0].status is AgentStatus.AVAILABLE
    assert promoted[0].available_at is None
