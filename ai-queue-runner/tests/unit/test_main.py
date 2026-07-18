import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.loader import load_config
from ai_queue_runner.domain.enums import AgentName, ErrorType, TaskStatus
from ai_queue_runner.domain.models import TaskRun
from ai_queue_runner.infrastructure.database import create_db_engine, make_session_factory
from ai_queue_runner.infrastructure.repositories import TaskRepository, TaskRunRepository
from ai_queue_runner.main import main


def write_cli_config(tmp_path):
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        "\n".join(
            (
                "[app]",
                f'allowed_project_roots = ["{allowed_root.as_posix()}"]',
                'db_path = "queue.db"',
                'logs_dir = "logs"',
            )
        ),
        encoding="utf-8",
    )
    return config_path, allowed_root


def cli_args(config_path, command, *arguments, as_json=False):
    args = ["--config", str(config_path)]
    if as_json:
        args.append("--json")
    return [*args, command, *map(str, arguments)]


def test_main_composes_desktop_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda config, **kwargs: calls.append((config, kwargs)) or 0,
    )

    assert main(["--no-tray"]) == 0
    assert len(calls) == 1
    assert calls[0][1] == {"smoke_test": False, "create_tray": False}


def test_main_prints_version_without_loading_config(capsys):
    assert main(["--version"]) == 0
    assert "AI Queue Runner 0.1.0" in capsys.readouterr().out


def test_main_reports_config_error(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "config error" in capsys.readouterr().err


def test_main_loads_app_data_config_when_no_cwd_config(tmp_path, monkeypatch):
    """Settings saved by the GUI dialog to the app-data config apply on next start."""
    monkeypatch.chdir(tmp_path)  # no config.toml in cwd
    app_data = tmp_path / "appdata"
    app_data.mkdir()
    (app_data / "config.toml").write_text(
        '[app]\nallowed_project_roots = ["C:/projects"]\n', encoding="utf-8"
    )
    monkeypatch.setattr("ai_queue_runner.main.default_app_data_dir", lambda: app_data)
    seen = []
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda config, **kwargs: seen.append(config) or 0,
    )

    assert main(["--no-tray"]) == 0
    assert [root.as_posix() for root in seen[0].app.allowed_project_roots] == ["C:/projects"]


def test_main_exports_effective_config_without_starting_ui(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("UI started")),
    )
    destination = tmp_path / "exports" / "config.toml"

    assert main(["--export-config", str(destination)]) == 0
    assert destination.is_file()
    assert "configuration exported" in capsys.readouterr().out


def test_main_registers_development_startup_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    app_data = tmp_path / "appdata"
    monkeypatch.setattr("ai_queue_runner.main.default_app_data_dir", lambda: app_data)
    calls = []
    monkeypatch.setattr(
        "ai_queue_runner.main.set_startup_enabled",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert main(["--startup", "enable"]) == 0
    assert calls[0][0][0] is True
    assert calls[0][1] == {"module": "ai_queue_runner"}
    assert calls[0][0][2].is_file()
    assert "Windows startup enabled" in capsys.readouterr().out


def test_cli_add_task_uses_real_service_and_rejects_outside_path(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    args = cli_args(
        config_path,
        "add-task",
        "--title",
        "CLI 작업",
        "--prompt",
        "테스트를 실행하세요.",
        "--project",
        project,
        "--agent",
        "codex",
        "--priority",
        75,
        "--auto-commit",
        as_json=True,
    )

    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    service = TaskService(make_session_factory(engine), config)
    created = service.get(payload["id"])

    assert created is not None
    assert created.title == "CLI 작업"
    assert created.agent_policy.value == "codex"
    assert created.priority == 75
    assert created.auto_commit is True
    assert created.project_path == str(project.resolve())
    engine.dispose()

    outside = tmp_path / "outside"
    outside.mkdir()
    rejected = cli_args(
        config_path,
        "add-task",
        "--title",
        "거부 작업",
        "--prompt",
        "실행하지 마세요.",
        "--project",
        outside,
    )
    assert main(rejected) == 2
    error = capsys.readouterr().err
    assert "요청을 처리할 수 없습니다" in error
    assert "outside allowed roots" in error
    assert "Traceback" not in error


def test_cli_scopes_tasks_by_project_creator_and_preserves_delete_events(
    tmp_path, capsys
):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    actor_prefix = [
        "--config",
        str(config_path),
        "--actor",
        "codex",
        "--actor-session",
        "session-42",
        "--json",
    ]
    assert main(
        [
            *actor_prefix,
            "add-task",
            "--title",
            "Codex 작업",
            "--prompt",
            "프로젝트를 점검하세요.",
            "--project",
            str(project),
        ]
    ) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["created_by"] == "codex"
    assert created["creator_session_id"] == "session-42"

    assert main(
        cli_args(
            config_path,
            "list-tasks",
            "--project",
            project,
            "--created-by",
            "codex",
            "--creator-session",
            "session-42",
            "--active",
            as_json=True,
        )
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [task["id"] for task in listed] == [created["id"]]

    assert main(
        [
            *actor_prefix,
            "delete-tasks",
            "--project",
            str(project),
            "--created-by",
            "codex",
            "--creator-session",
            "session-42",
        ]
    ) == 0
    deleted = json.loads(capsys.readouterr().out)
    assert deleted["deleted_ids"] == [created["id"]]
    assert deleted["skipped"] == []

    assert main(
        cli_args(
            config_path,
            "list-events",
            "--task-id",
            created["id"],
            as_json=True,
        )
    ) == 0
    events = json.loads(capsys.readouterr().out)
    assert [event["action"] for event in events] == ["deleted", "created"]
    assert all(event["actor"] == "codex" for event in events)


def test_cli_template_crud_reports_overwrite(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()

    save = cli_args(
        config_path,
        "save-template",
        "feature",
        "--title",
        "{feature} 구현",
        "--prompt",
        "{feature} 구현 후 pytest 통과",
        "--project",
        project,
        "--agent",
        "codex",
        "--auto-commit",
        as_json=True,
    )
    assert main(save) == 0
    assert json.loads(capsys.readouterr().out)["overwritten"] is False

    save[save.index("{feature} 구현") + 0] = "{feature} 구현 및 검증"
    assert main(save) == 0
    assert json.loads(capsys.readouterr().out)["overwritten"] is True

    assert main(cli_args(config_path, "list-templates", as_json=True)) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [template["name"] for template in listed] == ["feature"]

    assert main(cli_args(config_path, "show-template", "feature", as_json=True)) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["title"] == "{feature} 구현 및 검증"
    assert shown["agent_policy"] == "codex"
    assert shown["auto_commit"] is True

    assert main(cli_args(config_path, "delete-template", "feature", as_json=True)) == 0
    assert json.loads(capsys.readouterr().out) == {"deleted": "feature"}
    assert main(cli_args(config_path, "list-templates", as_json=True)) == 0
    assert json.loads(capsys.readouterr().out) == []


def test_cli_add_from_template_substitution_override_missing_and_dry_run(
    tmp_path,
    capsys,
):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    save = cli_args(
        config_path,
        "save-template",
        "feature",
        "--title",
        "{feature} 구현",
        "--prompt",
        "{feature} 구현 후 {check} 통과",
        "--project",
        project,
        "--priority",
        60,
    )
    assert main(save) == 0
    capsys.readouterr()

    dry_run = cli_args(
        config_path,
        "add-from-template",
        "feature",
        "--var",
        "feature=검색",
        "--var",
        "check=pytest/ruff",
        "--priority",
        95,
        "--dry-run",
        as_json=True,
    )
    assert main(dry_run) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["enqueued"] is False
    assert preview["task"] is None
    assert preview["draft"]["title"] == "검색 구현"
    assert preview["draft"]["priority"] == 95

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    service = TaskService(make_session_factory(engine), config)
    assert service.list() == []
    engine.dispose()

    missing = cli_args(
        config_path,
        "add-from-template",
        "feature",
        "--var",
        "feature=검색",
    )
    assert main(missing) == 2
    assert "missing template variables: check" in capsys.readouterr().err

    overridden = cli_args(
        config_path,
        "add-from-template",
        "feature",
        "--title",
        "직접 제목",
        "--prompt",
        "직접 프롬프트",
        "--agent",
        "claude",
        as_json=True,
    )
    assert main(overridden) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["enqueued"] is True
    assert payload["task"]["title"] == "직접 제목"
    assert payload["task"]["prompt"] == "직접 프롬프트"
    assert payload["task"]["agent_policy"] == "claude"


def test_cli_list_filter_show_and_json_include_stable_fields(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    first_args = cli_args(
        config_path,
        "add-task",
        "--title",
        "대기 작업",
        "--prompt",
        "대기합니다.",
        "--project",
        project,
        "--tag",
        "cli",
        as_json=True,
    )
    second_args = cli_args(
        config_path,
        "add-task",
        "--title",
        "취소 작업",
        "--prompt",
        "취소합니다.",
        "--project",
        project,
        as_json=True,
    )
    assert main(first_args) == 0
    first = json.loads(capsys.readouterr().out)
    assert main(second_args) == 0
    second = json.loads(capsys.readouterr().out)
    assert main(cli_args(config_path, "cancel-task", second["id"])) == 0
    capsys.readouterr()

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    with make_session_factory(engine)() as session:
        TaskRunRepository(session).add(
            TaskRun(
                task_id=first["id"],
                agent=AgentName.CODEX,
                attempt=1,
                command=["codex", "exec"],
                exit_code=1,
                result_summary="테스트 통과",
                error_type=ErrorType.UNKNOWN,
                fallback_to_agent=AgentName.CLAUDE,
                auto_commit_hash="abc123",
                auto_commit_result="committed",
            )
        )
        session.commit()
    engine.dispose()

    assert main(
        cli_args(config_path, "list-tasks", "--status", "waiting", "--json")
    ) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [task["id"] for task in listed] == [first["id"]]
    assert listed[0]["status"] == "waiting"
    assert listed[0]["tags"] == ["cli"]

    assert main(cli_args(config_path, "show-task", first["id"], "--json")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["id"] == first["id"]
    assert shown["project_path"] == str(project.resolve())
    assert shown["last_error"] is None
    assert shown["created_at"].endswith("Z")
    assert shown["recent_runs"][0]["result_summary"] == "테스트 통과"
    assert shown["recent_runs"][0]["fallback_to_agent"] == "claude"
    assert shown["recent_runs"][0]["auto_commit_hash"] == "abc123"
    assert shown["recent_runs"][0]["auto_commit_result"] == "committed"

    assert main(cli_args(config_path, "show-task", first["id"])) == 0
    text = capsys.readouterr().out
    assert "last_error: -" in text
    assert "fallback=codex->claude" in text
    assert "auto_commit=abc123" in text
    assert "result_summary=테스트 통과" in text


def test_cli_review_pending_and_reopen_workflow(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    assert main(
        cli_args(
            config_path,
            "add-task",
            "--title",
            "quality target",
            "--prompt",
            "implement it",
            "--project",
            project,
            "--agent",
            "codex",
            "--json",
        )
    ) == 0
    task_id = json.loads(capsys.readouterr().out)["id"]

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    with make_session_factory(engine)() as session:
        repo = TaskRepository(session)
        task = repo.get(task_id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        task.finished_at = datetime(2030, 1, 2, 3, 4, 5)
        session.commit()
    engine.dispose()

    assert main(
        cli_args(config_path, "pending-reviews", "--project", project, "--json")
    ) == 0
    pending = json.loads(capsys.readouterr().out)
    assert pending == [
        {
            "finished_at": "2030-01-02T03:04:05Z",
            "id": task_id,
            "project_path": str(project.resolve()),
            "title": "quality target",
        }
    ]

    assert main(
        cli_args(
            config_path,
            "review-task",
            task_id,
            "--verdict",
            "fail",
            "--score",
            2,
            "--notes",
            "missing test",
            "--reopen",
            "Add the missing test.",
            "--json",
        )
    ) == 0
    reviewed = json.loads(capsys.readouterr().out)
    assert reviewed["review"]["revision"] == 1
    assert reviewed["task"]["review_status"] == "fail"
    assert reviewed["rework_task"]["rework_of_task_id"] == task_id

    assert main(cli_args(config_path, "pending-reviews", "--json")) == 0
    assert json.loads(capsys.readouterr().out) == []
    assert main(cli_args(config_path, "show-task", task_id, "--json")) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["review_status"] == "fail"
    assert shown["review_score"] == 2
    assert shown["review_count"] == 1

    assert main(
        cli_args(
            config_path,
            "review-task",
            task_id,
            "--verdict",
            "pass",
            "--reopen",
            "invalid",
        )
    ) == 2
    assert "--reopen" in capsys.readouterr().err


def test_cli_cancel_refuses_succeeded_task_with_expected_exit(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    add_args = cli_args(
        config_path,
        "add-task",
        "--title",
        "완료 작업",
        "--prompt",
        "완료 처리합니다.",
        "--project",
        project,
        as_json=True,
    )
    assert main(add_args) == 0
    task_id = json.loads(capsys.readouterr().out)["id"]
    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    with make_session_factory(engine)() as session:
        repo = TaskRepository(session)
        task = repo.get(task_id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        session.commit()
    engine.dispose()

    assert main(cli_args(config_path, "cancel-task", task_id)) == 2
    error = capsys.readouterr().err
    assert "요청을 처리할 수 없습니다" in error
    assert "only waiting" in error
    assert "Traceback" not in error


def add_waiting_task(config_path, allowed_root, capsys, title="작업"):
    project = allowed_root / "project"
    project.mkdir(exist_ok=True)
    args = cli_args(
        config_path,
        "add-task",
        "--title",
        title,
        "--prompt",
        "프롬프트",
        "--project",
        project,
        as_json=True,
    )
    assert main(args) == 0
    return json.loads(capsys.readouterr().out)["id"]


def force_status(config_path, task_id, *statuses):
    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    with make_session_factory(engine)() as session:
        repo = TaskRepository(session)
        task = repo.get(task_id)
        for status in statuses:
            repo.set_status(task, status)
        session.commit()
    engine.dispose()


def test_cli_retry_task_requeues_failed_task(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    task_id = add_waiting_task(config_path, allowed_root, capsys)
    force_status(
        config_path,
        task_id,
        TaskStatus.RESERVED,
        TaskStatus.RUNNING,
        TaskStatus.FAILED,
    )

    assert main(cli_args(config_path, "retry-task", task_id, "--json")) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "waiting"
    assert payload["last_error"] is None


def test_cli_update_task_overlays_only_provided_flags(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    task_id = add_waiting_task(config_path, allowed_root, capsys, title="원래 제목")

    args = cli_args(
        config_path,
        "update-task",
        task_id,
        "--approval",
        "auto",
        "--priority",
        90,
        "--estimated-tokens",
        1_500,
        "--auto-commit",
        as_json=True,
    )
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["approval_mode"] == "auto"
    assert payload["priority"] == 90
    assert payload["estimated_tokens"] == 1_500
    assert payload["auto_commit"] is True
    assert payload["title"] == "원래 제목"
    assert payload["agent_policy"] == "any"

    assert main(
        cli_args(
            config_path,
            "update-task",
            task_id,
            "--no-auto-commit",
            as_json=True,
        )
    ) == 0
    assert json.loads(capsys.readouterr().out)["auto_commit"] is False

    conflict = cli_args(
        config_path,
        "update-task",
        task_id,
        "--not-before",
        "2030-01-01 09:00",
        "--clear-not-before",
    )
    assert main(conflict) == 2
    assert "함께 쓸 수 없습니다" in capsys.readouterr().err


def test_cli_sets_updates_lists_and_shows_recurring_rules(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    add_args = cli_args(
        config_path,
        "add-task",
        "--title",
        "반복 점검",
        "--prompt",
        "정기 점검을 실행합니다.",
        "--project",
        project,
        "--repeat",
        "daily",
        as_json=True,
    )

    assert main(add_args) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["recurrence"] == "daily"
    assert created["recurrence_interval_days"] == 1

    assert main(cli_args(config_path, "list-tasks")) == 0
    assert "daily" in capsys.readouterr().out

    assert main(
        cli_args(
            config_path,
            "update-task",
            created["id"],
            "--repeat",
            "weekly",
            as_json=True,
        )
    ) == 0
    weekly = json.loads(capsys.readouterr().out)
    assert weekly["recurrence"] == "weekly"
    assert weekly["recurrence_interval_days"] == 7

    assert main(
        cli_args(
            config_path,
            "update-task",
            created["id"],
            "--repeat",
            3,
            as_json=True,
        )
    ) == 0
    every_three_days = json.loads(capsys.readouterr().out)
    assert every_three_days["recurrence"] == "every 3 days"
    assert every_three_days["recurrence_interval_days"] == 3

    assert main(cli_args(config_path, "show-task", created["id"])) == 0
    shown = capsys.readouterr().out
    assert "recurrence: every 3 days" in shown
    assert "recurrence_interval_days: 3" in shown

    assert main(
        cli_args(
            config_path,
            "update-task",
            created["id"],
            "--clear-repeat",
            as_json=True,
        )
    ) == 0
    stopped = json.loads(capsys.readouterr().out)
    assert stopped["recurrence"] is None
    assert stopped["recurrence_interval_days"] is None


def test_cli_plan_batch_distributes_and_atomically_enqueues_tasks(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    plan_path = tmp_path / "workload.json"
    plan_path.write_text(
        json.dumps(
            {
                "budgets": {"claude": 1_000, "codex": 1_000},
                "tasks": [
                    {
                        "title": "large",
                        "prompt": "large work",
                        "project_path": str(project),
                        "agent_policy": "any",
                        "estimated_tokens": 700,
                        "approval_mode": "auto",
                    },
                    {
                        "title": "small",
                        "prompt": "small work",
                        "project_path": str(project),
                        "agent_policy": "any",
                        "estimated_tokens": 300,
                        "approval_mode": "auto",
                        "depends_on_indices": [0],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(cli_args(config_path, "plan-batch", plan_path, "--json")) == 0
    payload = json.loads(capsys.readouterr().out)

    assert payload["enqueued"] is True
    assert payload["assigned_tokens"] == {"claude": 700, "codex": 300}
    assert [task["assigned_agent"] for task in payload["tasks"]] == [
        "claude",
        "codex",
    ]
    assert all(task["id"] is not None for task in payload["tasks"])

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    service = TaskService(make_session_factory(engine), config)
    stored = service.list()
    assert [task.estimated_tokens for task in stored] == [700, 300]
    assert [task.agent_policy.value for task in stored] == ["claude", "codex"]
    assert stored[1].dependency_ids == (stored[0].id,)
    engine.dispose()


def test_cli_plan_batch_dry_run_and_overflow_leave_database_empty(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    plan_path = tmp_path / "workload.json"

    def write_plan(tokens):
        plan_path.write_text(
            json.dumps(
                {
                    "budgets": {"claude": 100, "codex": 100},
                    "tasks": [
                        {
                            "title": "planned",
                            "prompt": "work",
                            "project_path": str(project),
                            "estimated_tokens": tokens,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    write_plan(100)
    assert main(
        cli_args(config_path, "plan-batch", plan_path, "--dry-run", "--json")
    ) == 0
    assert json.loads(capsys.readouterr().out)["enqueued"] is False

    write_plan(201)
    assert main(cli_args(config_path, "plan-batch", plan_path, "--json")) == 2
    assert "remaining token budgets" in capsys.readouterr().err

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    assert TaskService(make_session_factory(engine), config).list() == []
    engine.dispose()


def test_cli_split_task_dry_run_then_records_relations_and_rewires_dependencies(
    tmp_path,
    capsys,
):
    config_path, allowed_root = write_cli_config(tmp_path)
    original_id = add_waiting_task(
        config_path,
        allowed_root,
        capsys,
        title="large task",
    )
    dependent_id = add_waiting_task(
        config_path,
        allowed_root,
        capsys,
        title="dependent task",
    )
    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    service = TaskService(make_session_factory(engine), config)
    dependent = service.get(dependent_id)
    service.update(
        dependent_id,
        TaskDraft(
            title=dependent.title,
            prompt=dependent.prompt,
            project_path=dependent.project_path,
            dependency_ids=[original_id],
        ),
    )
    engine.dispose()
    project = allowed_root / "project"
    plan_path = tmp_path / "split.json"
    plan_path.write_text(
        json.dumps(
            {
                "budgets": {"claude": 1_000, "codex": 1_000},
                "tasks": [
                    {
                        "title": "child one",
                        "prompt": "first part",
                        "project_path": str(project),
                        "agent_policy": "claude",
                        "estimated_tokens": 200,
                    },
                    {
                        "title": "child two",
                        "prompt": "second part",
                        "project_path": str(project),
                        "agent_policy": "codex",
                        "estimated_tokens": 300,
                        "depends_on_indices": [0],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    assert main(
        cli_args(
            config_path,
            "split-task",
            original_id,
            plan_path,
            "--dry-run",
            "--json",
        )
    ) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["split"] is False
    assert dry_run["original_task"]["status"] == "waiting"
    assert [task["id"] for task in dry_run["tasks"]] == [None, None]

    assert main(
        cli_args(config_path, "split-task", original_id, plan_path, "--json")
    ) == 0
    split = json.loads(capsys.readouterr().out)
    child_ids = [task["id"] for task in split["tasks"]]
    assert split["split"] is True
    assert split["original_task"]["status"] == "cancelled"
    assert split["original_task"]["split_child_ids"] == child_ids
    assert split["rewired_dependent_ids"] == [dependent_id]

    assert main(cli_args(config_path, "show-task", child_ids[1], "--json")) == 0
    shown_child = json.loads(capsys.readouterr().out)
    assert shown_child["split_parent_task_id"] == original_id
    assert shown_child["dependency_ids"] == [child_ids[0]]

    assert main(cli_args(config_path, "list-tasks", "--json")) == 0
    listed = json.loads(capsys.readouterr().out)
    listed_original = next(task for task in listed if task["id"] == original_id)
    assert listed_original["split_child_ids"] == child_ids

    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    service = TaskService(make_session_factory(engine), config)
    assert service.get(dependent_id).dependency_ids == tuple(child_ids)
    engine.dispose()


def test_cli_move_top_duplicate_archive_and_delete(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    first = add_waiting_task(config_path, allowed_root, capsys, title="첫 작업")
    second = add_waiting_task(config_path, allowed_root, capsys, title="둘째 작업")

    assert main(cli_args(config_path, "move-top", second, "--json")) == 0
    assert json.loads(capsys.readouterr().out)["priority"] == 100

    assert main(cli_args(config_path, "duplicate-task", first, "--json")) == 0
    duplicated = json.loads(capsys.readouterr().out)
    assert duplicated["id"] not in {first, second}
    assert duplicated["title"] == "첫 작업"
    assert duplicated["status"] == "waiting"

    force_status(
        config_path,
        first,
        TaskStatus.RESERVED,
        TaskStatus.RUNNING,
        TaskStatus.SUCCEEDED,
    )
    assert main(cli_args(config_path, "archive-task", first, "--json")) == 0
    assert json.loads(capsys.readouterr().out)["archived"] is True

    assert main(cli_args(config_path, "delete-task", duplicated["id"], "--json")) == 0
    assert json.loads(capsys.readouterr().out) == {"deleted": duplicated["id"]}
    assert main(cli_args(config_path, "show-task", duplicated["id"])) == 2
    assert "찾을 수 없습니다" in capsys.readouterr().err


def test_cli_set_agent_and_queue_status_report_automation_state(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    task_id = add_waiting_task(config_path, allowed_root, capsys)

    assert main(
        cli_args(
            config_path,
            "set-agent",
            "claude",
            "cooldown",
            "--until",
            "2030-04-05 09:00",
            "--json",
        )
    ) == 0
    agent_payload = json.loads(capsys.readouterr().out)
    assert agent_payload["agent"] == "claude"
    assert agent_payload["status"] == "cooldown"
    assert agent_payload["available_at"].endswith("Z")

    assert main(cli_args(config_path, "queue-status", "--json")) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["global_auto_run"] is False
    assert status_payload["task_counts"] == {"waiting": 1}
    assert status_payload["archived_count"] == 0
    assert status_payload["pending_confirmations"] == []
    agents = {record["agent"]: record for record in status_payload["agents"]}
    assert agents["claude"]["status"] == "cooldown"
    assert agents["codex"]["status"] == "unknown"

    force_status(config_path, task_id, TaskStatus.RESERVED)
    assert main(cli_args(config_path, "queue-status")) == 0
    text = capsys.readouterr().out
    assert "global_auto_run: false" in text
    assert "승인 대기(reserved):" in text


def test_cli_daily_summary_json_has_stable_machine_readable_structure(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    task_id = add_waiting_task(config_path, allowed_root, capsys, title="완료 작업")
    selected = "2032-06-07"
    local_finish = datetime.strptime(selected, "%Y-%m-%d").replace(hour=12).astimezone()
    finished_at = local_finish.astimezone(UTC).replace(tzinfo=None)
    config = load_config(config_path)
    engine = create_db_engine(config.app.db_path)
    with make_session_factory(engine)() as session:
        repo = TaskRepository(session)
        task = repo.get(task_id)
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        task.finished_at = finished_at
        TaskRunRepository(session).add(
            TaskRun(
                task_id=task_id,
                agent=AgentName.CODEX,
                attempt=1,
                command=["codex"],
                started_at=finished_at - timedelta(minutes=5),
                finished_at=finished_at,
                exit_code=0,
            )
        )
        session.commit()
    engine.dispose()

    assert main(
        cli_args(config_path, "daily-summary", "--date", selected, "--json")
    ) == 0
    payload = json.loads(capsys.readouterr().out)

    assert set(payload) == {"date", "period", "projects", "totals"}
    assert payload["date"] == selected
    assert set(payload["period"]) == {"start_utc", "end_utc"}
    assert len(payload["projects"]) == 1
    project = payload["projects"][0]
    assert set(project) == {
        "project_path",
        "task_counts",
        "total_runtime_seconds",
        "agent_run_counts",
        "fallback_count",
        "worktree_branches",
        "failed_tasks",
    }
    assert project["task_counts"] == {
        "succeeded": 1,
        "failed": 0,
        "blocked": 0,
        "cancelled": 0,
    }
    assert project["agent_run_counts"] == {"claude": 0, "codex": 1}
    assert project["total_runtime_seconds"] == 300
    assert payload["totals"]["usage_limit_wait_occurred"] == {
        "claude": False,
        "codex": False,
    }

    assert main(
        cli_args(config_path, "daily-summary", "--date", "2040-01-01", "--json")
    ) == 0
    empty = json.loads(capsys.readouterr().out)
    assert empty["projects"] == []
    assert empty["totals"]["project_count"] == 0
    assert empty["totals"]["task_counts"] == {
        "succeeded": 0,
        "failed": 0,
        "blocked": 0,
        "cancelled": 0,
    }


def test_cli_not_before_converts_local_time_to_naive_utc(tmp_path, capsys):
    config_path, allowed_root = write_cli_config(tmp_path)
    project = allowed_root / "project"
    project.mkdir()
    local_text = "2030-04-05 09:00"
    args = cli_args(
        config_path,
        "add-task",
        "--title",
        "예약 작업",
        "--prompt",
        "예약 시각을 검증합니다.",
        "--project",
        project,
        "--not-before",
        local_text,
        as_json=True,
    )

    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    expected = (
        datetime.strptime(local_text, "%Y-%m-%d %H:%M")
        .astimezone()
        .astimezone(UTC)
        .isoformat()
        .replace("+00:00", "Z")
    )
    assert payload["not_before"] == expected


def test_cli_subcommand_does_not_import_pyside6_in_fresh_process(tmp_path):
    config_path, _allowed_root = write_cli_config(tmp_path)
    args = cli_args(config_path, "list-tasks", as_json=True)
    script = (
        "import sys\n"
        "from ai_queue_runner.main import main\n"
        f"code = main({args!r})\n"
        "print('QT_IMPORTED=' + str(any(name.startswith('PySide6') "
        "for name in sys.modules)))\n"
        "raise SystemExit(code)\n"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert "QT_IMPORTED=False" in completed.stdout


def test_cli_unexpected_error_returns_three_without_traceback(
    tmp_path, monkeypatch, capsys
):
    config_path, _allowed_root = write_cli_config(tmp_path)

    def fail_unexpectedly(*_args):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(
        "ai_queue_runner.application.cli_service.run_cli_command",
        fail_unexpectedly,
    )

    assert main(cli_args(config_path, "list-tasks")) == 3
    error = capsys.readouterr().err
    assert "예기치 않은 오류가 발생했습니다" in error
    assert "Traceback" not in error
