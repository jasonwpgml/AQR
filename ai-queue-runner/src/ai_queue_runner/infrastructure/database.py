"""Engine/session creation and schema initialization."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from ai_queue_runner.domain.models import Base, Setting

SCHEMA_VERSION = 6


def _migrate_v1_to_v2(session: Session) -> None:
    columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "estimated_tokens" not in columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN estimated_tokens INTEGER NOT NULL "
                "DEFAULT 0 CHECK (estimated_tokens >= 0)"
            )
        )


def _migrate_v2_to_v3(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "fallback_agent" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN fallback_agent VARCHAR(6) "
                "CHECK (fallback_agent IN ('claude', 'codex'))"
            )
        )

    run_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_runs)")).mappings()
    }
    if "fallback_to_agent" not in run_columns:
        session.execute(
            text(
                "ALTER TABLE task_runs ADD COLUMN fallback_to_agent VARCHAR(6) "
                "CHECK (fallback_to_agent IN ('claude', 'codex'))"
            )
        )


def _migrate_v3_to_v4(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "recurrence_interval_days" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN recurrence_interval_days INTEGER "
                "CHECK (recurrence_interval_days IS NULL OR recurrence_interval_days >= 1)"
            )
        )
    if "recurrence_source_task_id" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN recurrence_source_task_id INTEGER "
                "REFERENCES tasks(id) ON DELETE SET NULL"
            )
        )
    session.execute(
        text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_tasks_recurrence_source_task_id "
            "ON tasks(recurrence_source_task_id)"
        )
    )


def _migrate_v4_to_v5(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "use_worktree" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN use_worktree BOOLEAN NOT NULL DEFAULT 0"
            )
        )
    if "worktree_path" not in task_columns:
        session.execute(text("ALTER TABLE tasks ADD COLUMN worktree_path TEXT"))
    if "worktree_branch" not in task_columns:
        session.execute(text("ALTER TABLE tasks ADD COLUMN worktree_branch TEXT"))

    run_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_runs)")).mappings()
    }
    if "worktree_path" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN worktree_path TEXT"))
    if "worktree_branch" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN worktree_branch TEXT"))


def _migrate_v5_to_v6(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "split_parent_task_id" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN split_parent_task_id INTEGER "
                "REFERENCES tasks(id) ON DELETE RESTRICT"
            )
        )
    session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tasks_split_parent_task_id "
            "ON tasks(split_parent_task_id)"
        )
    )


_MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
}


def create_db_engine(db_path: Path | str) -> Engine:
    """Create a SQLite engine. Pass ":memory:" for tests."""
    if isinstance(db_path, Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    # timeout: how long a writer waits for a competing write lock before
    # raising "database is locked" — needed when both agents reserve at once.
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"timeout": 30})

    # AIDEV-NOTE: SQLite defaults foreign_keys to OFF per connection; without this
    # listener the FK/CASCADE behavior silently disappears.
    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

    return engine


def init_db(engine: Engine) -> None:
    """Create tables and stamp/verify the schema version."""
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        stored = session.get(Setting, "schema_version")
        if stored is None:
            session.add(Setting(key="schema_version", value=SCHEMA_VERSION))
            session.commit()
            return
        version = int(stored.value)
        if version > SCHEMA_VERSION:
            raise RuntimeError(
                f"database schema v{version} is newer than app schema v{SCHEMA_VERSION}"
            )
        # AIDEV-NOTE: create_all() never adds columns to existing SQLite tables;
        # migrations must be safe to retry before advancing the stored version.
        while version < SCHEMA_VERSION:
            migration = _MIGRATIONS.get(version)
            if migration is None:
                raise RuntimeError(
                    f"no migration path from schema v{version} to v{SCHEMA_VERSION}"
                )
            migration(session)
            version += 1
            stored.value = version
            session.commit()


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
