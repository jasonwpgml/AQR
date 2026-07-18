from pathlib import Path

from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import TaskCreator, TaskEventAction, TaskStatus
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.repositories import TaskRepository


def _draft(project: Path, title: str, **overrides: object) -> TaskDraft:
    return TaskDraft(
        title=title,
        prompt=f"{title} 작업",
        project_path=project,
        **overrides,
    )


def test_creator_filters_and_management_events_survive_deletion(tmp_path: Path) -> None:
    first_project = tmp_path / "first"
    second_project = tmp_path / "second"
    first_project.mkdir()
    second_project.mkdir()
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    factory = make_session_factory(engine)
    service = TaskService(
        factory,
        config,
        actor=TaskCreator.CODEX,
        actor_session_id="codex-session-7",
    )

    first = service.create(_draft(first_project, "first"))
    service.create(
        _draft(
            first_project,
            "claude-owned",
            created_by=TaskCreator.CLAUDE,
            creator_session_id="claude-session-2",
        )
    )
    service.create(_draft(second_project, "other project"))

    assert first.created_by is TaskCreator.CODEX
    assert first.creator_session_id == "codex-session-7"
    assert [
        task.title
        for task in service.list(
            project_path=first_project,
            created_by=TaskCreator.CODEX,
            creator_session_id="codex-session-7",
        )
    ] == ["first"]

    service.update(first.id, _draft(first_project, "updated"))
    service.cancel(first.id)
    service.delete(first.id)

    assert service.get(first.id) is None
    events = service.list_events(first.id)
    assert [event.action for event in events] == [
        TaskEventAction.DELETED,
        TaskEventAction.CANCELLED,
        TaskEventAction.UPDATED,
        TaskEventAction.CREATED,
    ]
    assert all(event.actor is TaskCreator.CODEX for event in events)
    assert all(event.actor_session_id == "codex-session-7" for event in events)
    assert all(event.project_path == str(first_project.resolve()) for event in events)
    engine.dispose()


def test_project_delete_removes_dependency_chain_and_reports_active_skip(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    factory = make_session_factory(engine)
    service = TaskService(factory, config, actor=TaskCreator.CLAUDE)

    parent = service.create(_draft(project, "parent"))
    child = service.create(
        _draft(project, "child", dependency_ids=[parent.id])
    )
    active = service.create(_draft(project, "active"))
    with factory() as session:
        repo = TaskRepository(session)
        repo.set_status(repo.get(active.id), TaskStatus.RESERVED)
        session.commit()

    result = service.delete_project_tasks(project, created_by=TaskCreator.CLAUDE)

    assert result.matched_count == 3
    assert result.deleted_ids == (parent.id, child.id)
    assert [(item.id, item.reason) for item in result.skipped] == [
        (active.id, "reserved or running tasks must be cancelled before deletion")
    ]
    assert [task.id for task in service.list(project_path=project, active=True)] == [
        active.id
    ]
    deleted_events = [
        event
        for event in service.list_events(project_path=project)
        if event.action is TaskEventAction.DELETED
    ]
    assert {event.task_id for event in deleted_events} == {parent.id, child.id}
    engine.dispose()
