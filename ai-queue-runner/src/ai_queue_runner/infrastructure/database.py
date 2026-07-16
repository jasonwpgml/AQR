"""Engine/session creation and schema initialization."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from ai_queue_runner.domain.models import Base, Setting

SCHEMA_VERSION = 1


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
        if version < SCHEMA_VERSION:
            # AIDEV-TODO: add a migration registry when SCHEMA_VERSION first bumps;
            # v1 has nothing to migrate from.
            raise RuntimeError(
                f"no migration path from schema v{version} to v{SCHEMA_VERSION}"
            )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)
