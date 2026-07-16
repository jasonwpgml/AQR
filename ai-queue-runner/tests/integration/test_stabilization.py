from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.exporter import export_config
from ai_queue_runner.config.loader import load_config
from ai_queue_runner.infrastructure.backup import BackupError, backup_database
from ai_queue_runner.infrastructure.windows_startup import build_startup_command


def test_online_backup_is_reopenable_and_consistent(tmp_path: Path):
    source = tmp_path / "queue.db"
    with sqlite3.connect(source) as database:
        database.execute("PRAGMA journal_mode=WAL")
        database.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, value TEXT)")
        database.executemany("INSERT INTO items(value) VALUES (?)", [("a",), ("b",)])
        database.commit()

        destination = backup_database(source, tmp_path / "backups" / "queue.db")

    with sqlite3.connect(destination) as backup:
        assert backup.execute("PRAGMA integrity_check").fetchone() == ("ok",)
        assert backup.execute("SELECT value FROM items ORDER BY id").fetchall() == [
            ("a",),
            ("b",),
        ]


def test_backup_rejects_missing_or_same_destination(tmp_path: Path):
    with pytest.raises(BackupError, match="does not exist"):
        backup_database(tmp_path / "missing.db", tmp_path / "backup.db")

    source = tmp_path / "queue.db"
    sqlite3.connect(source).close()
    with pytest.raises(BackupError, match="must differ"):
        backup_database(source, source)


def test_exported_config_roundtrips_through_loader(tmp_path: Path):
    config = AppConfig()
    config.app.db_path = tmp_path / "data" / "queue.db"
    config.app.logs_dir = tmp_path / "logs"
    config.app.allowed_project_roots = [tmp_path]
    config.scheduler.poll_interval_seconds = 7
    config.agents["claude"].limit_patterns = ["session limit", "사용량 제한"]
    destination = export_config(config, tmp_path / "export.toml")

    loaded = load_config(destination)

    assert loaded == config


def test_startup_command_quotes_paths_with_spaces(tmp_path: Path):
    executable = tmp_path / "Program Files" / "AIQueueRunner.exe"
    config = tmp_path / "My Config" / "config.toml"

    command = build_startup_command(executable, config)

    assert f'"{executable.resolve()}"' in command
    assert f'"{config.resolve()}"' in command
    assert "--config" in command


def test_startup_command_supports_python_module_launch(tmp_path: Path):
    executable = tmp_path / "Python 3.12" / "python.exe"
    config = tmp_path / "config.toml"

    command = build_startup_command(
        executable,
        config,
        module="ai_queue_runner",
    )

    assert f'"{executable.resolve()}"' in command
    assert "-m ai_queue_runner" in command
    assert command.endswith(f"--config {config.resolve()}")
