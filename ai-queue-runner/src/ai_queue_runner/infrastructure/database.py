"""Engine/session creation and schema initialization."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Connection, Engine, create_engine, event, inspect, text
from sqlalchemy.orm import Session, sessionmaker

from ai_queue_runner.domain.models import Base, Setting

SCHEMA_VERSION = 11


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


def _migrate_v6_to_v7(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "rework_of_task_id" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN rework_of_task_id INTEGER "
                "REFERENCES tasks(id) ON DELETE RESTRICT"
            )
        )
    session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_tasks_rework_of_task_id "
            "ON tasks(rework_of_task_id)"
        )
    )
    session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS task_reviews ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE, "
            "verdict VARCHAR(4) NOT NULL CHECK (verdict IN ('pass', 'fail')), "
            "score INTEGER CHECK (score IS NULL OR score BETWEEN 1 AND 5), "
            "notes TEXT, reviewed_at DATETIME NOT NULL)"
        )
    )
    session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_task_reviews_task_id "
            "ON task_reviews(task_id)"
        )
    )


def _migrate_v7_to_v8(session: Session) -> None:
    session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS task_templates ("
            "name TEXT NOT NULL PRIMARY KEY, "
            "title TEXT NOT NULL, "
            "prompt TEXT NOT NULL, "
            "project_path TEXT, "
            "agent_policy VARCHAR(6) NOT NULL "
            "CHECK (agent_policy IN ('claude', 'codex', 'any')), "
            "priority INTEGER NOT NULL DEFAULT 50 CHECK (priority BETWEEN 0 AND 100), "
            "estimated_tokens INTEGER NOT NULL DEFAULT 0 CHECK (estimated_tokens >= 0), "
            "approval_mode VARCHAR(7) NOT NULL DEFAULT 'confirm' "
            "CHECK (approval_mode IN ('auto', 'confirm')), "
            "max_retries INTEGER NOT NULL DEFAULT 2 CHECK (max_retries >= 0), "
            "retry_delay_minutes INTEGER NOT NULL DEFAULT 10 "
            "CHECK (retry_delay_minutes >= 0), "
            "recurrence_interval_days INTEGER "
            "CHECK (recurrence_interval_days IS NULL OR recurrence_interval_days >= 1), "
            "not_before DATETIME, "
            "tags_json JSON NOT NULL, "
            "dependency_ids_json JSON NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "updated_at DATETIME NOT NULL)"
        )
    )


def _migrate_v8_to_v9(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "auto_commit" not in task_columns:
        session.execute(
            text("ALTER TABLE tasks ADD COLUMN auto_commit BOOLEAN NOT NULL DEFAULT 0")
        )

    run_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_runs)")).mappings()
    }
    if "auto_commit_hash" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN auto_commit_hash TEXT"))
    if "auto_commit_result" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN auto_commit_result TEXT"))

    template_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_templates)")).mappings()
    }
    if "auto_commit" not in template_columns:
        session.execute(
            text(
                "ALTER TABLE task_templates ADD COLUMN auto_commit BOOLEAN NOT NULL DEFAULT 0"
            )
        )


def _migrate_v9_to_v10(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "created_by" not in task_columns:
        session.execute(
            text(
                "ALTER TABLE tasks ADD COLUMN created_by VARCHAR(7) NOT NULL "
                "DEFAULT 'unknown' CHECK (created_by IN "
                "('unknown', 'user', 'claude', 'codex', 'system'))"
            )
        )
    if "creator_session_id" not in task_columns:
        session.execute(text("ALTER TABLE tasks ADD COLUMN creator_session_id TEXT"))

    template_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_templates)")).mappings()
    }
    if "created_by" not in template_columns:
        session.execute(
            text(
                "ALTER TABLE task_templates ADD COLUMN created_by VARCHAR(7) NOT NULL "
                "DEFAULT 'unknown' CHECK (created_by IN "
                "('unknown', 'user', 'claude', 'codex', 'system'))"
            )
        )
    if "creator_session_id" not in template_columns:
        session.execute(
            text("ALTER TABLE task_templates ADD COLUMN creator_session_id TEXT")
        )

    session.execute(
        text(
            "CREATE TABLE IF NOT EXISTS task_events ("
            "id INTEGER NOT NULL PRIMARY KEY, "
            "task_id INTEGER NOT NULL, "
            "task_created_at DATETIME NOT NULL, "
            "action VARCHAR(9) NOT NULL CHECK (action IN "
            "('created', 'updated', 'cancelled', 'retried', 'promoted', "
            "'archived', 'reviewed', 'split', 'deleted')), "
            "actor VARCHAR(7) NOT NULL CHECK (actor IN "
            "('unknown', 'user', 'claude', 'codex', 'system')), "
            "actor_session_id TEXT, "
            "project_path TEXT NOT NULL, "
            "task_title TEXT NOT NULL, "
            "details_json JSON NOT NULL, "
            "created_at DATETIME NOT NULL)"
        )
    )
    session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_task_events_task_id "
            "ON task_events(task_id)"
        )
    )
    session.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_task_events_project_path "
            "ON task_events(project_path)"
        )
    )


def _migrate_v10_to_v11(session: Session) -> None:
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    if "draft_pr" not in task_columns:
        session.execute(
            text("ALTER TABLE tasks ADD COLUMN draft_pr BOOLEAN NOT NULL DEFAULT 0")
        )

    run_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_runs)")).mappings()
    }
    if "draft_pr_url" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN draft_pr_url TEXT"))
    if "draft_pr_result" not in run_columns:
        session.execute(text("ALTER TABLE task_runs ADD COLUMN draft_pr_result TEXT"))

    template_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(task_templates)")).mappings()
    }
    if "draft_pr" not in template_columns:
        session.execute(
            text(
                "ALTER TABLE task_templates ADD COLUMN draft_pr BOOLEAN NOT NULL DEFAULT 0"
            )
        )


_MIGRATIONS = {
    1: _migrate_v1_to_v2,
    2: _migrate_v2_to_v3,
    3: _migrate_v3_to_v4,
    4: _migrate_v4_to_v5,
    5: _migrate_v5_to_v6,
    6: _migrate_v6_to_v7,
    7: _migrate_v7_to_v8,
    8: _migrate_v8_to_v9,
    9: _migrate_v9_to_v10,
    10: _migrate_v10_to_v11,
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
    with engine.connect() as connection:
        # AIDEV-NOTE: every process takes the same SQLite writer lock before schema
        # inspection. Otherwise two AI CLI reads can race the same ALTER TABLE migration.
        connection.exec_driver_sql("BEGIN IMMEDIATE")
        had_tasks = inspect(connection).has_table("tasks")
        legacy_version = None
        if had_tasks:
            legacy_version = _infer_legacy_schema_version(connection)
        Base.metadata.create_all(connection)
        with Session(bind=connection) as session:
            stored = session.get(Setting, "schema_version")
            if stored is None:
                version = legacy_version if legacy_version is not None else SCHEMA_VERSION
                stored = Setting(key="schema_version", value=version)
                session.add(stored)
            else:
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
            session.flush()
            connection.commit()


def _infer_legacy_schema_version(session: Session | Connection) -> int:
    """Infer old packaged DBs that predate the schema_version setting."""
    task_columns = {
        row["name"]
        for row in session.execute(text("PRAGMA table_info(tasks)")).mappings()
    }
    table_names = {
        row["name"]
        for row in session.execute(
            text("SELECT name FROM sqlite_master WHERE type = 'table'")
        ).mappings()
    }
    if "draft_pr" in task_columns:
        return 11
    if "created_by" in task_columns and "task_events" in table_names:
        return 10
    if "auto_commit" in task_columns:
        return 9
    if "task_templates" in table_names:
        return 8
    if "rework_of_task_id" in task_columns and "task_reviews" in table_names:
        return 7
    if "split_parent_task_id" in task_columns:
        return 6
    if "use_worktree" in task_columns:
        return 5
    if "recurrence_interval_days" in task_columns:
        return 4
    if "fallback_agent" in task_columns:
        return 3
    if "estimated_tokens" in task_columns:
        return 2
    return 1


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
