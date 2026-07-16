"""Verified SQLite online backups safe for WAL-mode databases."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class BackupError(RuntimeError):
    """Raised when a database backup cannot be created or verified."""


def backup_database(source_path: Path | str, destination_path: Path | str) -> Path:
    source = Path(source_path).resolve()
    destination = Path(destination_path).resolve()
    if not source.is_file():
        raise BackupError(f"source database does not exist: {source}")
    if source == destination:
        raise BackupError("backup destination must differ from source database")
    destination.parent.mkdir(parents=True, exist_ok=True)

    try:
        with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
            source_db.backup(backup_db)
            result = backup_db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error as exc:
        destination.unlink(missing_ok=True)
        raise BackupError(f"database backup failed: {exc}") from exc

    if result != ("ok",):
        destination.unlink(missing_ok=True)
        raise BackupError(f"backup integrity check failed: {result}")
    return destination
