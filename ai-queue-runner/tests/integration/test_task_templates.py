from pathlib import Path

import pytest

from ai_queue_runner.application.task_template_service import (
    TaskTemplateDraft,
    TaskTemplateService,
)
from ai_queue_runner.domain.enums import TaskCreator
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)


@pytest.fixture()
def templates(tmp_path: Path):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    service = TaskTemplateService(make_session_factory(engine))
    yield service
    engine.dispose()


def test_template_save_overwrite_get_list_delete_roundtrip(templates) -> None:
    created, overwritten = templates.save(
        "feature",
        TaskTemplateDraft(
            title="{feature} 구현",
            prompt="{feature} 구현 후 pytest를 실행하세요.",
            agent_policy="codex",
            priority=70,
            estimated_tokens=12000,
            auto_commit=True,
            tags=["implementation"],
        ),
    )
    assert overwritten is False
    assert created.name == "feature"
    assert created.auto_commit is True
    assert created.created_by is TaskCreator.USER
    assert templates.get("feature") == created
    assert templates.list() == [created]

    updated, overwritten = templates.save(
        "feature",
        TaskTemplateDraft(
            title="{feature} 구현과 검증",
            prompt="{feature} 구현 후 pytest와 ruff를 실행하세요.",
            agent_policy="any",
            priority=90,
        ),
    )
    assert overwritten is True
    assert updated.created_at == created.created_at
    assert updated.title == "{feature} 구현과 검증"
    assert updated.priority == 90

    templates.delete("feature")
    assert templates.get("feature") is None
    assert templates.list() == []


def test_template_render_substitutes_rejects_missing_and_prioritizes_overrides(
    templates,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    templates.save(
        "feature",
        TaskTemplateDraft(
            title="{feature} 구현",
            prompt="{feature} 구현 후 {check} 통과",
            project_path=project,
            agent_policy="codex",
            priority=70,
        ),
    )

    rendered = templates.render(
        "feature", {"feature": "검색", "check": "pytest/ruff"}
    )
    assert rendered.title == "검색 구현"
    assert rendered.prompt == "검색 구현 후 pytest/ruff 통과"
    assert rendered.auto_commit is False
    assert rendered.created_by is TaskCreator.USER

    with pytest.raises(ValueError, match="missing template variables: check"):
        templates.render("feature", {"feature": "검색"})

    overridden = templates.render(
        "feature",
        {},
        title="직접 제목",
        prompt="직접 프롬프트",
        agent_policy="claude",
        priority=99,
    )
    assert overridden.title == "직접 제목"
    assert overridden.prompt == "직접 프롬프트"
    assert overridden.agent_policy.value == "claude"
    assert overridden.priority == 99
