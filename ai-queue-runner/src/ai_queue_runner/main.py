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
    args = parser.parse_args(argv)

    if args.version:
        print(f"AI Queue Runner {__version__}")
        return 0

    try:
        config = load_config(args.config)
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

    setup_logging(config.app.logs_dir, config.app.log_level)
    logger.info("AI Queue Runner %s starting", __version__)
    logger.info(
        "global_auto_run=%s agents=%s",
        config.app.global_auto_run,
        ",".join(config.agents),
    )
    return run_desktop_app(
        config,
        smoke_test=args.smoke_test,
        create_tray=not args.no_tray,
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
    smoke_test: bool = False,
    create_tray: bool = True,
) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QSystemTrayIcon

    from ai_queue_runner.application.agent_service import AgentService
    from ai_queue_runner.application.runtime import QueueRuntime
    from ai_queue_runner.application.task_service import TaskService
    from ai_queue_runner.infrastructure.adapters import ClaudeAdapter, CodexAdapter
    from ai_queue_runner.infrastructure.database import (
        create_db_engine,
        init_db,
        make_session_factory,
    )
    from ai_queue_runner.ui.main_window import MainWindow

    engine = create_db_engine(config.app.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    adapters = {
        "claude": ClaudeAdapter(config.agents["claude"]),
        "codex": CodexAdapter(config.agents["codex"]),
    }
    task_service = TaskService(session_factory, config)
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
