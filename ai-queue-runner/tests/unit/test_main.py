import json
import subprocess
import sys
from datetime import UTC, datetime

from ai_queue_runner.application.task_service import TaskService
from ai_queue_runner.config.loader import load_config
from ai_queue_runner.domain.enums import AgentName, TaskStatus
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
                exit_code=0,
                result_summary="테스트 통과",
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

    assert main(cli_args(config_path, "show-task", first["id"])) == 0
    text = capsys.readouterr().out
    assert "last_error: -" in text
    assert "result_summary=테스트 통과" in text


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
