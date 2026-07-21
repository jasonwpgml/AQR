from __future__ import annotations

from pathlib import Path

import pytest

from ai_queue_runner.application.prompt_template_service import (
    PromptTemplateService,
    TemplateView,
)
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)


def test_prompt_template_roundtrip_survives_engine_reopen(tmp_path: Path) -> None:
    db_path = tmp_path / "queue.db"
    engine = create_db_engine(db_path)
    init_db(engine)
    service = PromptTemplateService(make_session_factory(engine))

    service.save("  Review  ", "  Review this change.  ")
    service.save("Build", "Build the feature.")
    assert service.list() == [
        TemplateView("Build", "Build the feature."),
        TemplateView("Review", "Review this change."),
    ]
    assert service.save("Review", "Review tests too.") == TemplateView(
        "Review", "Review tests too."
    )
    assert service.rename("Review", "Audit") == TemplateView("Audit", "Review tests too.")
    engine.dispose()

    reopened_engine = create_db_engine(db_path)
    init_db(reopened_engine)
    reopened = PromptTemplateService(make_session_factory(reopened_engine))
    assert reopened.list() == [
        TemplateView("Audit", "Review tests too."),
        TemplateView("Build", "Build the feature."),
    ]

    reopened.delete("Audit")
    assert reopened.list() == [TemplateView("Build", "Build the feature.")]
    reopened_engine.dispose()


def test_prompt_template_validation_and_caps(tmp_path: Path) -> None:
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    service = PromptTemplateService(make_session_factory(engine))

    with pytest.raises(ValueError, match="name must not be empty"):
        service.save("  ", "prompt")
    with pytest.raises(ValueError, match="prompt must not be empty"):
        service.save("name", "\n\t")
    with pytest.raises(ValueError, match="60 characters"):
        service.save("x" * 61, "prompt")

    service.save("first", "one")
    service.save("second", "two")
    with pytest.raises(ValueError, match="already exists"):
        service.rename("first", "second")

    for index in range(2, 200):
        service.save(f"template-{index:03d}", "prompt")
    service.save("first", "overwrite at the cap")
    with pytest.raises(ValueError, match="cannot exceed 200"):
        service.save("one-too-many", "prompt")
    engine.dispose()


def test_task_creation_remains_independent_from_prompt_templates(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    session_factory = make_session_factory(engine)
    template_service = PromptTemplateService(session_factory)
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    task_service = TaskService(session_factory, config)

    selected = template_service.save("Implement", "Implement the requested feature.")
    created = task_service.create(
        TaskDraft(
            title="Prompt template task",
            prompt=selected.prompt,
            project_path=project,
        )
    )
    template_service.save("Implement", "A later template revision.")

    assert task_service.get(created.id).prompt == "Implement the requested feature."
    engine.dispose()
