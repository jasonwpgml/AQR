from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from tests.fakes import FakeAdapter

from ai_queue_runner.application.runtime import QueueRuntime
from ai_queue_runner.application.task_service import TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    TaskStatus,
)
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.git_worktrees import GitWorktreeManager
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    TaskRepository,
    TaskRunRepository,
)
from ai_queue_runner.main import main


@pytest.fixture()
def git_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    project.mkdir()
    _git(project, "init", "-b", "main")
    _git(project, "config", "user.email", "tests@example.invalid")
    _git(project, "config", "user.name", "Queue Tests")
    (project / "README.md").write_text("base\n", encoding="utf-8")
    _git(project, "add", "README.md")
    _git(project, "commit", "-m", "initial")
    return project


def test_lock_conflict_creates_executes_and_records_worktree(
    tmp_path: Path,
    git_project: Path,
) -> None:
    factory, config, engine = _make_app(tmp_path, worktrees_enabled=True)
    locked_id, task_id = _add_locked_pair(factory, git_project)
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; Path('executed.txt').write_text(str(Path.cwd()))",
    ]
    runtime = QueueRuntime(
        factory,
        config,
        {AgentName.CODEX: FakeAdapter(name=AgentName.CODEX, command=command)},
    )
    try:
        assert runtime.tick_once() == (task_id,)
        _wait_until(
            lambda: TaskService(factory, config).get(task_id).status is TaskStatus.SUCCEEDED
        )

        task = TaskService(factory, config).get(task_id)
        runs = TaskService(factory, config).list_runs(task_id)
        assert task.project_path == str(git_project.resolve())
        assert task.use_worktree is True
        assert task.worktree_branch == f"aqr/T{task_id}"
        assert task.worktree_path == runs[-1].worktree_path
        assert task.worktree_branch == runs[-1].worktree_branch
        assert Path(task.worktree_path, "executed.txt").is_file()
        assert not (git_project / "executed.txt").exists()
        assert _git(Path(task.worktree_path), "branch", "--show-current").stdout.strip() == (
            f"aqr/T{task_id}"
        )
        assert TaskService(factory, config).get(locked_id).status is TaskStatus.RUNNING
    finally:
        runtime.shutdown()
        engine.dispose()


def test_disabled_and_non_git_projects_fall_back_to_exclusive_lock(
    tmp_path: Path,
    git_project: Path,
) -> None:
    factory, config, engine = _make_app(tmp_path / "off", worktrees_enabled=False)
    _locked_id, task_id = _add_locked_pair(factory, git_project)
    runtime = QueueRuntime(
        factory,
        config,
        {AgentName.CODEX: FakeAdapter(name=AgentName.CODEX)},
    )
    try:
        assert runtime.tick_once() == ()
        task = TaskService(factory, config).get(task_id)
        assert task.status is TaskStatus.WAITING
        assert task.worktree_path is None
    finally:
        runtime.shutdown()
        engine.dispose()

    plain_project = tmp_path / "plain"
    plain_project.mkdir()
    factory, config, engine = _make_app(tmp_path / "nongit", worktrees_enabled=True)
    _locked_id, task_id = _add_locked_pair(factory, plain_project)
    runtime = QueueRuntime(
        factory,
        config,
        {AgentName.CODEX: FakeAdapter(name=AgentName.CODEX)},
    )
    try:
        assert runtime.tick_once() == ()
        task = TaskService(factory, config).get(task_id)
        assert task.status is TaskStatus.WAITING
        assert task.use_worktree is False
        assert task.worktree_path is None
        assert "exclusive fallback" in task.last_error
    finally:
        runtime.shutdown()
        engine.dispose()


def test_cleanup_cli_refuses_unmerged_branch_then_removes_merged_worktree(
    tmp_path: Path,
    git_project: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config_path = _write_config(tmp_path, git_project)
    config = _load_test_config(config_path)
    engine = create_db_engine(config.app.db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        task = TaskRepository(session).add(
            Task(
                title="completed worktree",
                prompt="done",
                project_path=str(git_project.resolve()),
                agent_policy=AgentPolicy.CODEX,
            )
        )
        session.commit()
        task_id = task.id

    manager = GitWorktreeManager(config.app.worktrees_dir)
    prepared = manager.prepare(git_project, task_id)
    (prepared.path / "change.txt").write_text("task change\n", encoding="utf-8")
    _git(prepared.path, "add", "change.txt")
    _git(prepared.path, "commit", "-m", "task change")
    with factory() as session:
        task = TaskRepository(session).get(task_id)
        task.use_worktree = True
        task.worktree_path = str(prepared.path)
        task.worktree_branch = prepared.branch
        repo = TaskRepository(session)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        TaskRunRepository(session).add(
            TaskRun(
                task_id=task_id,
                agent=AgentName.CODEX,
                attempt=1,
                command=["codex"],
                worktree_path=str(prepared.path),
                worktree_branch=prepared.branch,
            )
        )
        repo.set_status(task, TaskStatus.SUCCEEDED)
        session.commit()
    engine.dispose()

    args = ["--config", str(config_path), "--json", "cleanup-worktree", str(task_id)]
    assert main(args) == 2
    assert "not merged" in capsys.readouterr().err
    assert prepared.path.is_dir()

    _git(git_project, "merge", "--no-ff", prepared.branch, "-m", "merge task")
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["worktree_branch"] == prepared.branch
    assert not prepared.path.exists()
    assert main(["--config", str(config_path), "--json", "show-task", str(task_id)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["use_worktree"] is False
    assert shown["worktree_path"] is None
    assert shown["worktree_branch"] is None
    assert shown["recent_runs"][0]["worktree_path"] == str(prepared.path)
    assert shown["recent_runs"][0]["worktree_branch"] == prepared.branch

    assert main(args) == 2
    error = capsys.readouterr().err
    assert "no recorded worktree" in error

    branch_lookup = _git(
        git_project,
        "show-ref",
        "--verify",
        f"refs/heads/{prepared.branch}",
    )
    assert branch_lookup.returncode == 0


def test_restart_reuses_orphan_worktree_without_blocking_schedule(
    tmp_path: Path,
    git_project: Path,
) -> None:
    factory, config, engine = _make_app(tmp_path, worktrees_enabled=True)
    _locked_id, task_id = _add_locked_pair(factory, git_project)
    manager = GitWorktreeManager(config.app.worktrees_dir)
    orphan = manager.prepare(git_project, task_id)
    with factory() as session:
        task = TaskRepository(session).get(task_id)
        task.use_worktree = True
        task.worktree_path = str(orphan.path)
        task.worktree_branch = orphan.branch
        session.commit()

    command = [sys.executable, "-c", "from pathlib import Path; Path('resumed.txt').touch()"]
    restarted_runtime = QueueRuntime(
        factory,
        config,
        {AgentName.CODEX: FakeAdapter(name=AgentName.CODEX, command=command)},
    )
    try:
        assert restarted_runtime.tick_once() == (task_id,)
        _wait_until(
            lambda: TaskService(factory, config).get(task_id).status is TaskStatus.SUCCEEDED
        )
        task = TaskService(factory, config).get(task_id)
        assert task.worktree_path == str(orphan.path)
        assert (orphan.path / "resumed.txt").is_file()
    finally:
        restarted_runtime.shutdown()
        engine.dispose()


def _make_app(tmp_path: Path, *, worktrees_enabled: bool):
    tmp_path.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    factory = make_session_factory(engine)
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path.parent]
    config.app.logs_dir = tmp_path / "logs"
    config.app.worktrees_dir = tmp_path / "worktrees"
    config.app.global_auto_run = True
    config.scheduler.worktree_parallel_enabled = worktrees_enabled
    return factory, config, engine


def _add_locked_pair(factory, project: Path) -> tuple[int, int]:
    with factory() as session:
        repo = TaskRepository(session)
        locked = repo.add(
            Task(
                title="locked",
                prompt="still running",
                project_path=str(project.resolve()),
                agent_policy=AgentPolicy.CLAUDE,
                assigned_agent=AgentName.CLAUDE,
                status=TaskStatus.RUNNING,
            )
        )
        candidate = repo.add(
            Task(
                title="parallel",
                prompt="run isolated",
                project_path=str(project.resolve()),
                agent_policy=AgentPolicy.CODEX,
                approval_mode=ApprovalMode.AUTO,
            )
        )
        claude = AgentStateRepository(session).get_or_create(AgentName.CLAUDE)
        claude.status = AgentStatus.BUSY
        claude.current_task_id = locked.id
        codex = AgentStateRepository(session).get_or_create(AgentName.CODEX)
        codex.status = AgentStatus.AVAILABLE
        session.commit()
        return locked.id, candidate.id


def _write_config(tmp_path: Path, project: Path) -> Path:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[app]",
                f'allowed_project_roots = ["{project.parent.as_posix()}"]',
                'db_path = "queue.db"',
                'logs_dir = "logs"',
                'worktrees_dir = "worktrees"',
                "[scheduler]",
                "worktree_parallel_enabled = true",
            )
        ),
        encoding="utf-8",
    )
    return config_path


def _load_test_config(config_path: Path) -> AppConfig:
    from ai_queue_runner.config.loader import load_config

    return load_config(config_path)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=30,
    )
    if completed.returncode != 0:
        pytest.fail(completed.stderr or completed.stdout)
    return completed


def _wait_until(predicate, *, timeout_seconds: float = 5) -> None:
    deadline = time.monotonic() + timeout_seconds
    while not predicate():
        if time.monotonic() >= deadline:
            pytest.fail("condition was not met before the deadline")
        time.sleep(0.01)
