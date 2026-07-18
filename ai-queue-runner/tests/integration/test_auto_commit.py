from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from tests.fakes import FakeAdapter

from ai_queue_runner.application.execution_service import ExecutionService
from ai_queue_runner.application.scheduler_service import reserve_next_task
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import AgentName, AgentStatus, TaskStatus
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
)


def _git(project: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(project), *args],
        capture_output=True,
        check=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
    )


def _init_repository(project: Path) -> None:
    project.mkdir()
    _git(project, "init")
    _git(project, "config", "user.name", "AQR Test")
    _git(project, "config", "user.email", "aqr-test@example.invalid")
    (project / "README.md").write_text("initial\n", encoding="utf-8")
    _git(project, "add", "-A")
    _git(project, "commit", "-m", "initial")


async def _execute(
    tmp_path: Path,
    project: Path,
    *,
    config_enabled: bool,
    task_enabled: bool,
    script: str,
):
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    config.app.logs_dir = tmp_path / "logs"
    config.scheduler.auto_commit_enabled = config_enabled
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        state = AgentStateRepository(session).get_or_create(AgentName.CLAUDE)
        state.status = AgentStatus.AVAILABLE
        task = TaskRepository(session).add(
            Task(
                title="Generate result",
                prompt="write the requested file",
                project_path=str(project),
                agent_policy=AgentName.CLAUDE.value,
                auto_commit=task_enabled,
            )
        )
        session.commit()
        reserved = reserve_next_task(session, config, AgentName.CLAUDE)
        assert reserved is not None and reserved.id == task.id
        adapter = FakeAdapter(
            name=AgentName.CLAUDE,
            command=[sys.executable, "-c", script],
        )
        run = await ExecutionService(config, ProcessRunner()).execute(
            session,
            reserved,
            adapter,
        )
        session.refresh(run)
        session.refresh(reserved)
        result = (
            reserved.status,
            run.auto_commit_hash,
            run.auto_commit_result,
            run.id,
        )
    engine.dispose()
    return result


@pytest.mark.parametrize(
    ("config_enabled", "task_enabled"),
    [(False, True), (True, False)],
)
async def test_auto_commit_requires_both_opt_ins(
    tmp_path: Path,
    config_enabled: bool,
    task_enabled: bool,
) -> None:
    project = tmp_path / "project"
    _init_repository(project)

    status, commit_hash, result, _run_id = await _execute(
        tmp_path,
        project,
        config_enabled=config_enabled,
        task_enabled=task_enabled,
        script=(
            "from pathlib import Path; "
            "Path('generated.txt').write_text('generated\\n', encoding='utf-8')"
        ),
    )

    assert status is TaskStatus.SUCCEEDED
    assert commit_hash is None
    assert result is None
    assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == "1"


async def test_successful_run_commits_changes_and_records_hash(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _init_repository(project)

    status, commit_hash, result, run_id = await _execute(
        tmp_path,
        project,
        config_enabled=True,
        task_enabled=True,
        script=(
            "from pathlib import Path; "
            "Path('generated.txt').write_text('generated\\n', encoding='utf-8')"
        ),
    )

    head = _git(project, "rev-parse", "HEAD").stdout.strip()
    assert status is TaskStatus.SUCCEEDED
    assert commit_hash == head
    assert result == "committed"
    assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    assert _git(project, "log", "-1", "--pretty=%s").stdout.strip() == (
        "aqr: Generate result (task #1)"
    )
    body = _git(project, "log", "-1", "--pretty=%b").stdout
    assert f"Execution ID: {run_id}" in body
    assert "Agent: claude" in body


async def test_auto_commit_uses_the_prepared_worktree_as_git_cwd(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _init_repository(project)
    worktrees_root = tmp_path / "worktrees"
    worktrees_root.mkdir()
    worktree = worktrees_root / "T1"
    _git(project, "worktree", "add", "-b", "aqr/T1", str(worktree), "HEAD")

    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    config.app.logs_dir = tmp_path / "logs"
    config.app.worktrees_dir = worktrees_root
    config.scheduler.auto_commit_enabled = True
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    with make_session_factory(engine)() as session:
        state = AgentStateRepository(session).get_or_create(AgentName.CLAUDE)
        state.status = AgentStatus.AVAILABLE
        TaskRepository(session).add(
            Task(
                title="Worktree result",
                prompt="write in the isolated checkout",
                project_path=str(project),
                agent_policy=AgentName.CLAUDE.value,
                auto_commit=True,
                use_worktree=True,
                worktree_path=str(worktree),
                worktree_branch="aqr/T1",
            )
        )
        session.commit()
        reserved = reserve_next_task(session, config, AgentName.CLAUDE)
        assert reserved is not None
        adapter = FakeAdapter(
            name=AgentName.CLAUDE,
            command=[
                sys.executable,
                "-c",
                "from pathlib import Path; Path('isolated.txt').write_text('done\\n')",
            ],
        )

        run = await ExecutionService(config, ProcessRunner()).execute(
            session,
            reserved,
            adapter,
        )

        assert reserved.status is TaskStatus.SUCCEEDED
        assert run.auto_commit_result == "committed"
        assert run.auto_commit_hash == _git(worktree, "rev-parse", "HEAD").stdout.strip()
        assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == "1"
        assert _git(worktree, "rev-list", "--count", "HEAD").stdout.strip() == "2"
    engine.dispose()


async def test_auto_commit_skips_when_run_makes_no_changes(tmp_path: Path) -> None:
    project = tmp_path / "project"
    _init_repository(project)

    status, commit_hash, result, _run_id = await _execute(
        tmp_path,
        project,
        config_enabled=True,
        task_enabled=True,
        script="print('no repository changes')",
    )

    assert status is TaskStatus.SUCCEEDED
    assert commit_hash is None
    assert result == "skipped: no changes after execution"


async def test_auto_commit_skips_non_repository(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()

    status, commit_hash, result, _run_id = await _execute(
        tmp_path,
        project,
        config_enabled=True,
        task_enabled=True,
        script=(
            "from pathlib import Path; "
            "Path('generated.txt').write_text('generated\\n', encoding='utf-8')"
        ),
    )

    assert status is TaskStatus.SUCCEEDED
    assert commit_hash is None
    assert result == "skipped: not a Git repository"


async def test_auto_commit_refuses_repository_dirty_before_execution(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _init_repository(project)
    (project / "existing.txt").write_text("older work\n", encoding="utf-8")

    status, commit_hash, result, _run_id = await _execute(
        tmp_path,
        project,
        config_enabled=True,
        task_enabled=True,
        script=(
            "from pathlib import Path; "
            "Path('generated.txt').write_text('generated\\n', encoding='utf-8')"
        ),
    )

    assert status is TaskStatus.SUCCEEDED
    assert commit_hash is None
    assert result == "skipped: repository had changes before execution"
    assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == "1"


async def test_commit_failure_does_not_change_successful_task_status(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    _init_repository(project)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")

    status, commit_hash, result, _run_id = await _execute(
        tmp_path,
        project,
        config_enabled=True,
        task_enabled=True,
        script=(
            "from pathlib import Path; "
            "Path('generated.txt').write_text('generated\\n', encoding='utf-8')"
        ),
    )

    assert status is TaskStatus.SUCCEEDED
    assert commit_hash is None
    assert result is not None and result.startswith("failed:")
    assert _git(project, "rev-list", "--count", "HEAD").stdout.strip() == "1"
