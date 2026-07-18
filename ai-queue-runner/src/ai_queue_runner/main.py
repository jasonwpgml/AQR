"""Application entry point and desktop dependency composition."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ai_queue_runner import __version__
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.exporter import export_config
from ai_queue_runner.config.loader import ConfigError, default_app_data_dir, load_config
from ai_queue_runner.infrastructure.backup import BackupError, backup_database
from ai_queue_runner.infrastructure.logging_config import setup_logging
from ai_queue_runner.infrastructure.windows_startup import set_startup_enabled

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-queue-runner",
        description="Local task queue scheduler for Claude and Codex CLIs",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    parser.add_argument(
        "--actor",
        choices=("unknown", "user", "claude", "codex", "system"),
        default="user",
        help="identity recorded for task management events",
    )
    parser.add_argument(
        "--actor-session",
        metavar="ID",
        help="optional caller session ID recorded with task management events",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print subcommand output as machine-readable JSON",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    maintenance = parser.add_mutually_exclusive_group()
    maintenance.add_argument(
        "--backup",
        type=Path,
        metavar="PATH",
        help="back up the queue database, verify it, and exit",
    )
    maintenance.add_argument(
        "--export-config",
        type=Path,
        metavar="PATH",
        help="export the effective configuration as TOML and exit",
    )
    maintenance.add_argument(
        "--startup",
        choices=("enable", "disable"),
        help="enable or disable per-user Windows startup and exit",
    )
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--no-tray", action="store_true", help=argparse.SUPPRESS)
    subcommands = parser.add_subparsers(dest="command")
    add_task = subcommands.add_parser("add-task", help="add a task without starting the GUI")
    add_task.add_argument("--title", required=True, help="task title")
    add_task.add_argument("--prompt", required=True, help="prompt sent to the selected AI")
    add_task.add_argument("--project", type=Path, required=True, help="absolute project path")
    add_task.add_argument("--agent", choices=("claude", "codex", "any"), default="any")
    add_task.add_argument("--priority", type=int, default=50, metavar="0-100")
    add_task.add_argument(
        "--estimated-tokens",
        type=int,
        default=0,
        metavar="N",
        help="estimated workload tokens; required to be positive in plan-batch",
    )
    add_task.add_argument("--approval", choices=("auto", "confirm"), default="confirm")
    add_task.add_argument(
        "--auto-commit",
        action="store_true",
        help="request an automatic Git commit after a successful run",
    )
    add_task.add_argument(
        "--not-before",
        metavar="YYYY-MM-DD HH:MM",
        help="local time; stored internally as UTC",
    )
    add_task.add_argument("--max-retries", type=int, default=2, metavar="N")
    add_task.add_argument(
        "--repeat",
        metavar="daily|weekly|N",
        help="enqueue a new occurrence after success; N is an interval in days",
    )
    add_task.add_argument(
        "--tag",
        action="extend",
        nargs="+",
        default=[],
        metavar="TAG",
    )
    add_task.add_argument(
        "--depends-on",
        action="extend",
        nargs="+",
        type=int,
        default=[],
        metavar="ID",
    )
    _add_subcommand_json_option(add_task)

    save_template = subcommands.add_parser(
        "save-template", help="save or overwrite a reusable task template"
    )
    save_template.add_argument("name", metavar="NAME")
    save_template.add_argument("--title", required=True, help="template task title")
    save_template.add_argument("--prompt", required=True, help="template task prompt")
    save_template.add_argument("--project", type=Path, help="default absolute project path")
    save_template.add_argument("--agent", choices=("claude", "codex", "any"), default="any")
    save_template.add_argument("--priority", type=int, default=50, metavar="0-100")
    save_template.add_argument("--estimated-tokens", type=int, default=0, metavar="N")
    save_template.add_argument("--approval", choices=("auto", "confirm"), default="confirm")
    save_template.add_argument("--auto-commit", action="store_true")
    save_template.add_argument("--not-before", metavar="YYYY-MM-DD HH:MM")
    save_template.add_argument("--max-retries", type=int, default=2, metavar="N")
    save_template.add_argument("--retry-delay", type=int, default=10, metavar="MINUTES")
    save_template.add_argument("--repeat", metavar="daily|weekly|N")
    save_template.add_argument("--tag", action="extend", nargs="+", default=[], metavar="TAG")
    save_template.add_argument(
        "--depends-on", action="extend", nargs="+", type=int, default=[], metavar="ID"
    )
    _add_subcommand_json_option(save_template)

    list_templates = subcommands.add_parser(
        "list-templates", help="list reusable task templates"
    )
    _add_subcommand_json_option(list_templates)

    show_template = subcommands.add_parser(
        "show-template", help="show one reusable task template"
    )
    show_template.add_argument("name", metavar="NAME")
    _add_subcommand_json_option(show_template)

    delete_template = subcommands.add_parser(
        "delete-template", help="delete a reusable task template"
    )
    delete_template.add_argument("name", metavar="NAME")
    _add_subcommand_json_option(delete_template)

    add_from_template = subcommands.add_parser(
        "add-from-template", help="render a template and add one task"
    )
    add_from_template.add_argument("name", metavar="NAME")
    add_from_template.add_argument(
        "--var", action="append", default=[], metavar="KEY=VALUE"
    )
    add_from_template.add_argument("--title", help="override the rendered title")
    add_from_template.add_argument("--prompt", help="override the rendered prompt")
    add_from_template.add_argument("--project", type=Path, help="override project path")
    add_from_template.add_argument("--agent", choices=("claude", "codex", "any"))
    add_from_template.add_argument("--priority", type=int, metavar="0-100")
    add_from_template.add_argument("--estimated-tokens", type=int, metavar="N")
    add_from_template.add_argument("--approval", choices=("auto", "confirm"))
    _add_auto_commit_toggle(add_from_template)
    add_from_template.add_argument("--not-before", metavar="YYYY-MM-DD HH:MM")
    add_from_template.add_argument("--max-retries", type=int, metavar="N")
    add_from_template.add_argument("--retry-delay", type=int, metavar="MINUTES")
    add_from_template.add_argument("--repeat", metavar="daily|weekly|N")
    add_from_template.add_argument(
        "--tag", action="extend", nargs="+", default=None, metavar="TAG"
    )
    add_from_template.add_argument(
        "--depends-on", action="extend", nargs="+", type=int, default=None, metavar="ID"
    )
    add_from_template.add_argument(
        "--dry-run", action="store_true", help="validate and preview without creating a task"
    )
    _add_subcommand_json_option(add_from_template)

    list_tasks = subcommands.add_parser("list-tasks", help="list queue tasks")
    list_mode = list_tasks.add_mutually_exclusive_group()
    list_mode.add_argument(
        "--status",
        choices=(
            "waiting",
            "reserved",
            "running",
            "succeeded",
            "failed",
            "retry_wait",
            "blocked",
            "cancelled",
            "archived",
        ),
    )
    list_mode.add_argument(
        "--active",
        action="store_true",
        help="show waiting, reserved, running, retry-wait, and blocked tasks",
    )
    list_tasks.add_argument("--project", type=Path, help="filter by absolute project path")
    list_tasks.add_argument(
        "--created-by",
        choices=("unknown", "user", "claude", "codex", "system"),
    )
    list_tasks.add_argument("--creator-session", metavar="ID")
    _add_subcommand_json_option(list_tasks)

    show_task = subcommands.add_parser("show-task", help="show one task and recent runs")
    show_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(show_task)

    review_task = subcommands.add_parser(
        "review-task", help="record a quality review for a succeeded task"
    )
    review_task.add_argument("task_id", type=int, metavar="ID")
    review_task.add_argument("--verdict", choices=("pass", "fail"), required=True)
    review_task.add_argument("--score", type=int, metavar="1-5")
    review_task.add_argument("--notes", metavar="TEXT")
    review_task.add_argument(
        "--reopen",
        metavar="PROMPT",
        help="with verdict fail, atomically enqueue a linked rework task",
    )
    _add_subcommand_json_option(review_task)

    pending_reviews = subcommands.add_parser(
        "pending-reviews", help="list succeeded tasks without a quality review"
    )
    pending_reviews.add_argument("--project", type=Path, help="absolute project path")
    _add_subcommand_json_option(pending_reviews)

    cancel_task = subcommands.add_parser("cancel-task", help="cancel a queued task")
    cancel_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(cancel_task)

    retry_task = subcommands.add_parser(
        "retry-task", help="requeue a failed or blocked task"
    )
    retry_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(retry_task)

    update_task = subcommands.add_parser(
        "update-task", help="update fields of a waiting task; omitted flags keep current values"
    )
    update_task.add_argument("task_id", type=int, metavar="ID")
    update_task.add_argument("--title", help="new task title")
    update_task.add_argument("--prompt", help="new prompt")
    update_task.add_argument("--project", type=Path, help="new absolute project path")
    update_task.add_argument("--agent", choices=("claude", "codex", "any"))
    update_task.add_argument("--priority", type=int, metavar="0-100")
    update_task.add_argument("--estimated-tokens", type=int, metavar="N")
    update_task.add_argument("--approval", choices=("auto", "confirm"))
    _add_auto_commit_toggle(update_task)
    update_task.add_argument(
        "--not-before",
        metavar="YYYY-MM-DD HH:MM",
        help="local time; stored internally as UTC",
    )
    update_task.add_argument(
        "--clear-not-before",
        action="store_true",
        help="remove the scheduled start time",
    )
    update_task.add_argument("--max-retries", type=int, metavar="N")
    update_task.add_argument("--retry-delay", type=int, metavar="MINUTES")
    update_task.add_argument(
        "--repeat",
        metavar="daily|weekly|N",
        help="set the recurrence interval",
    )
    update_task.add_argument(
        "--clear-repeat",
        action="store_true",
        help="stop the recurring chain after this task",
    )
    update_task.add_argument(
        "--tag",
        action="extend",
        nargs="+",
        default=None,
        metavar="TAG",
        help="replace all tags",
    )
    update_task.add_argument(
        "--depends-on",
        action="extend",
        nargs="+",
        type=int,
        default=None,
        metavar="ID",
        help="replace all dependencies",
    )
    _add_subcommand_json_option(update_task)

    duplicate_task = subcommands.add_parser(
        "duplicate-task", help="create a new waiting copy of a task"
    )
    duplicate_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(duplicate_task)

    move_top = subcommands.add_parser(
        "move-top", help="move a waiting task to the top of the queue"
    )
    move_top.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(move_top)

    archive_task = subcommands.add_parser(
        "archive-task", help="archive a succeeded task"
    )
    archive_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(archive_task)

    delete_task = subcommands.add_parser(
        "delete-task", help="delete a task and its run history"
    )
    delete_task.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(delete_task)

    delete_tasks = subcommands.add_parser(
        "delete-tasks",
        help="delete every eligible task in an explicit project scope",
    )
    delete_tasks.add_argument(
        "--project", type=Path, required=True, help="absolute project path"
    )
    delete_tasks.add_argument(
        "--created-by",
        choices=("unknown", "user", "claude", "codex", "system"),
    )
    delete_tasks.add_argument("--creator-session", metavar="ID")
    delete_tasks.add_argument(
        "--status",
        action="append",
        choices=(
            "waiting",
            "succeeded",
            "failed",
            "retry_wait",
            "blocked",
            "cancelled",
            "archived",
        ),
        help="repeat to limit deletion to selected statuses",
    )
    _add_subcommand_json_option(delete_tasks)

    list_events = subcommands.add_parser(
        "list-events", help="list durable task management events"
    )
    list_events.add_argument("--task-id", type=int, metavar="ID")
    list_events.add_argument("--project", type=Path)
    list_events.add_argument(
        "--actor",
        dest="event_actor",
        choices=("unknown", "user", "claude", "codex", "system"),
    )
    list_events.add_argument("--limit", type=int, default=100, metavar="N")
    _add_subcommand_json_option(list_events)

    set_agent = subcommands.add_parser(
        "set-agent", help="set manual agent availability"
    )
    set_agent.add_argument("agent", choices=("claude", "codex"))
    set_agent.add_argument("state", choices=("available", "cooldown", "disabled"))
    set_agent.add_argument(
        "--until",
        metavar="YYYY-MM-DD HH:MM",
        help="local time when a cooldown agent becomes available again",
    )
    _add_subcommand_json_option(set_agent)

    queue_status = subcommands.add_parser(
        "queue-status", help="summarize tasks, agents, and automation settings"
    )
    _add_subcommand_json_option(queue_status)

    daily_summary = subcommands.add_parser(
        "daily-summary", help="summarize one local date of queue activity by project"
    )
    daily_summary.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="local date; defaults to today",
    )
    _add_subcommand_json_option(daily_summary)

    plan_batch = subcommands.add_parser(
        "plan-batch",
        help="distribute a token-budgeted JSON workload and enqueue it atomically",
    )
    plan_batch.add_argument("plan_path", type=Path, metavar="PATH")
    plan_batch.add_argument(
        "--dry-run",
        action="store_true",
        help="show deterministic assignments without creating tasks",
    )
    _add_subcommand_json_option(plan_batch)
    split_task = subcommands.add_parser(
        "split-task",
        help="replace a queued task with an atomic dependent child batch",
    )
    split_task.add_argument("task_id", type=int, metavar="ID")
    split_task.add_argument("plan_path", type=Path, metavar="PATH")
    split_task.add_argument(
        "--dry-run",
        action="store_true",
        help="validate and show the split without changing the queue",
    )
    _add_subcommand_json_option(split_task)
    cleanup_worktree = subcommands.add_parser(
        "cleanup-worktree",
        help="remove a completed task worktree after verifying all changes are merged",
    )
    cleanup_worktree.add_argument("task_id", type=int, metavar="ID")
    _add_subcommand_json_option(cleanup_worktree)
    args = parser.parse_args(argv)

    if args.version:
        print(f"AI Queue Runner {__version__}")
        return 0

    active_config_path = _select_active_config_path(args.config)
    try:
        # AIDEV-NOTE: load from active_config_path, not args.config — the GUI settings
        # dialog saves to the app-data config.toml, which must apply on the next start.
        # A missing explicit --config still falls through so load_config raises for it.
        config = load_config(active_config_path if active_config_path.exists() else args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    try:
        maintenance_result = _run_maintenance(args, config)
    except (BackupError, OSError, TypeError, ValueError) as exc:
        print(f"maintenance error: {exc}", file=sys.stderr)
        return 3
    if maintenance_result is not None:
        print(maintenance_result)
        return 0

    if args.command is not None:
        from ai_queue_runner.application.cli_service import CliUsageError, run_cli_command

        try:
            output = run_cli_command(args, config)
        except CliUsageError as exc:
            print(f"요청을 처리할 수 없습니다: {exc}", file=sys.stderr)
            return 2
        except Exception as exc:  # CLI boundary: never expose tracebacks to callers.
            print(f"예기치 않은 오류가 발생했습니다: {exc}", file=sys.stderr)
            return 3
        print(output)
        return 0

    setup_logging(config.app.logs_dir, config.app.log_level)
    logger.info("AI Queue Runner %s starting", __version__)
    logger.info(
        "global_auto_run=%s agents=%s",
        config.app.global_auto_run,
        ",".join(config.agents),
    )
    desktop_options = {
        "smoke_test": args.smoke_test,
        "create_tray": not args.no_tray,
    }
    if args.config is not None or active_config_path == Path.cwd() / "config.toml":
        desktop_options["active_config_path"] = active_config_path
    return run_desktop_app(config, **desktop_options)


def _select_active_config_path(explicit_path: Path | None) -> Path:
    if explicit_path is not None:
        return explicit_path
    cwd_path = Path.cwd() / "config.toml"
    return cwd_path if cwd_path.exists() else default_app_data_dir() / "config.toml"


def _add_subcommand_json_option(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--json",
        action="store_true",
        default=argparse.SUPPRESS,
        help="print this command's output as machine-readable JSON",
    )


def _add_auto_commit_toggle(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--auto-commit",
        dest="auto_commit",
        action="store_true",
        default=None,
        help="request an automatic Git commit after a successful run",
    )
    group.add_argument(
        "--no-auto-commit",
        dest="auto_commit",
        action="store_false",
        help="disable automatic Git commit for the task",
    )


def _run_maintenance(args: argparse.Namespace, config: AppConfig) -> str | None:
    if args.backup is not None:
        destination = backup_database(config.app.db_path, args.backup)
        return f"database backup created: {destination}"
    if args.export_config is not None:
        destination = export_config(config, args.export_config)
        return f"configuration exported: {destination}"
    if args.startup is None:
        return None

    config_path = args.config
    if config_path is None:
        config_path = default_app_data_dir() / "config.toml"
        if args.startup == "enable":
            export_config(config, config_path)

    frozen = bool(getattr(sys, "frozen", False))
    set_startup_enabled(
        args.startup == "enable",
        sys.executable,
        config_path,
        module=None if frozen else "ai_queue_runner",
    )
    return f"Windows startup {args.startup}d"


def run_desktop_app(
    config: AppConfig,
    *,
    active_config_path: Path | None = None,
    smoke_test: bool = False,
    create_tray: bool = True,
) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from ai_queue_runner.application.agent_service import AgentService
    from ai_queue_runner.application.prompt_template_service import PromptTemplateService
    from ai_queue_runner.application.runtime import QueueRuntime
    from ai_queue_runner.application.task_service import TaskService
    from ai_queue_runner.infrastructure.adapters import ClaudeAdapter, CodexAdapter
    from ai_queue_runner.infrastructure.database import (
        create_db_engine,
        init_db,
        make_session_factory,
    )
    from ai_queue_runner.ui.main_window import MainWindow

    if active_config_path is None:
        active_config_path = _select_active_config_path(None)

    engine = create_db_engine(config.app.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    adapters = {
        "claude": ClaudeAdapter(config.agents["claude"]),
        "codex": CodexAdapter(config.agents["codex"]),
    }
    task_service = TaskService(session_factory, config)
    prompt_template_service = PromptTemplateService(session_factory)
    agent_service = AgentService(session_factory)
    runtime = QueueRuntime(session_factory, config, adapters)

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName("AI Queue Runner")
    app.setApplicationVersion(__version__)
    tray_available = create_tray and QSystemTrayIcon.isSystemTrayAvailable()
    app.setQuitOnLastWindowClosed(not tray_available)
    window = MainWindow(
        task_service,
        agent_service,
        runtime,
        adapters,
        config=config,
        active_config_path=active_config_path,
        prompt_template_service=prompt_template_service,
        create_tray=tray_available,
    )
    _offer_startup_recovery(window, session_factory)
    app.aboutToQuit.connect(lambda: runtime.shutdown(wait=True))
    window.show()
    if smoke_test:
        QTimer.singleShot(250, app.quit)

    try:
        return app.exec()
    finally:
        runtime.shutdown(wait=True)
        engine.dispose()


def _offer_startup_recovery(parent, session_factory) -> None:
    from PySide6.QtWidgets import QMessageBox

    from ai_queue_runner.application.recovery_service import (
        find_orphaned_running_tasks,
        recover_orphaned_task,
    )
    from ai_queue_runner.domain.enums import TaskStatus
    from ai_queue_runner.infrastructure.repositories import TaskRepository

    with session_factory() as session:
        orphaned_runs = find_orphaned_running_tasks(session)

    for orphaned in orphaned_runs:
        if orphaned.pid_alive and not orphaned.pid_identity_verified:
            QMessageBox.warning(
                parent,
                "중단된 작업 복구",
                f"작업 #{orphaned.task_id}의 PID {orphaned.pid}가 존재하지만 프로세스 "
                "생성 시각을 확인할 수 없습니다. 안전을 위해 자동 복구하지 않습니다.",
            )
            continue
        process_note = (
            f"PID {orphaned.pid}가 아직 실행 중입니다. 선택한 복구 작업은 이 프로세스와 "
            "하위 프로세스를 종료합니다.\n\n"
            if orphaned.pid_alive
            else ""
        )
        box = QMessageBox(parent)
        box.setWindowTitle("중단된 작업 복구")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText(
            f"작업 #{orphaned.task_id}이 실행 중 상태로 남아 있습니다.\n\n"
            f"{process_note}어떻게 복구할까요?"
        )
        waiting_button = box.addButton("대기열로 복구", QMessageBox.ButtonRole.AcceptRole)
        failed_button = box.addButton("실패로 처리", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("나중에", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked not in {waiting_button, failed_button}:
            continue
        with session_factory() as session:
            task = TaskRepository(session).get(orphaned.task_id)
            if task is None or task.status is not TaskStatus.RUNNING:
                continue
            recover_orphaned_task(
                session,
                task,
                to_status=(
                    TaskStatus.WAITING if clicked is waiting_button else TaskStatus.FAILED
                ),
                kill_if_alive=orphaned.pid_alive,
            )
