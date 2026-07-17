import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from ai_queue_runner.domain.enums import AgentName, AgentStatus, ErrorType, TaskStatus
from ai_queue_runner.domain.models import Task, TaskRun
from ai_queue_runner.domain.transitions import InvalidTransitionError
from ai_queue_runner.infrastructure.database import (
    SCHEMA_VERSION,
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.repositories import (
    AgentStateRepository,
    SettingsRepository,
    TaskRepository,
    TaskRunRepository,
)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "queue.db"


@pytest.fixture()
def session(db_path):
    engine = create_db_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    with factory() as session:
        yield session
    engine.dispose()


def make_task(**overrides) -> Task:
    defaults = dict(
        title="test task",
        prompt="do something",
        project_path="C:/projects/demo",
        agent_policy="any",
    )
    defaults.update(overrides)
    return Task(**defaults)


class TestTaskCrud:
    def test_create_applies_defaults(self, session):
        task = TaskRepository(session).add(make_task())
        session.commit()
        assert task.id is not None
        assert task.status is TaskStatus.WAITING
        assert task.priority == 50
        assert task.retry_count == 0
        assert task.estimated_tokens == 0
        assert task.tags == []
        assert task.created_at is not None

    def test_update_and_delete(self, session):
        repo = TaskRepository(session)
        task = repo.add(make_task())
        session.commit()

        task.priority = 90
        session.commit()
        assert repo.get(task.id).priority == 90

        repo.delete(task)
        session.commit()
        assert repo.get(task.id) is None

    def test_list_filters_by_status(self, session):
        repo = TaskRepository(session)
        t1 = repo.add(make_task(title="a"))
        repo.add(make_task(title="b"))
        repo.set_status(t1, TaskStatus.CANCELLED)
        session.commit()
        waiting = repo.list(status=TaskStatus.WAITING)
        assert [t.title for t in waiting] == ["b"]

    def test_invalid_agent_policy_rejected(self):
        with pytest.raises(ValueError):
            make_task(agent_policy="gemini")

    @pytest.mark.parametrize("priority", [-1, 101])
    def test_priority_out_of_range_rejected(self, priority):
        with pytest.raises(ValueError):
            make_task(priority=priority)

    def test_set_status_enforces_state_machine(self, session):
        repo = TaskRepository(session)
        task = repo.add(make_task())
        with pytest.raises(InvalidTransitionError):
            repo.set_status(task, TaskStatus.RUNNING)  # waiting -> running is illegal
        repo.set_status(task, TaskStatus.RESERVED)
        repo.set_status(task, TaskStatus.RUNNING)
        repo.set_status(task, TaskStatus.SUCCEEDED)
        session.commit()
        assert repo.get(task.id).status is TaskStatus.SUCCEEDED


class TestPersistence:
    def test_data_survives_engine_restart(self, db_path):
        engine = create_db_engine(db_path)
        init_db(engine)
        factory = make_session_factory(engine)
        with factory() as session:
            TaskRepository(session).add(make_task(title="survivor"))
            session.commit()
        engine.dispose()

        engine2 = create_db_engine(db_path)
        init_db(engine2)  # idempotent on an existing DB
        with make_session_factory(engine2)() as session:
            tasks = TaskRepository(session).list()
            assert [t.title for t in tasks] == ["survivor"]
        engine2.dispose()

    def test_schema_version_is_stamped(self, session):
        assert SettingsRepository(session).get("schema_version") == SCHEMA_VERSION

    def test_schema_v1_is_migrated_with_estimated_tokens(self, tmp_path):
        db_path = tmp_path / "v1.db"
        engine = create_db_engine(db_path)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql("INSERT INTO tasks (id) VALUES (1)")
            connection.exec_driver_sql(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json JSON NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value_json) VALUES ('schema_version', '1')"
            )

        init_db(engine)

        columns = {column["name"]: column for column in inspect(engine).get_columns("tasks")}
        assert columns["estimated_tokens"]["nullable"] is False
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT estimated_tokens FROM tasks WHERE id = 1"
            ).scalar_one() == 0
        with make_session_factory(engine)() as session:
            assert SettingsRepository(session).get("schema_version") == SCHEMA_VERSION
        engine.dispose()

    def test_schema_v2_is_migrated_with_fallback_columns(self, tmp_path):
        db_path = tmp_path / "v2.db"
        engine = create_db_engine(db_path)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY, estimated_tokens INTEGER "
                "NOT NULL DEFAULT 0)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json JSON NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value_json) VALUES ('schema_version', '2')"
            )

        init_db(engine)

        task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
        run_columns = {
            column["name"] for column in inspect(engine).get_columns("task_runs")
        }
        assert "fallback_agent" in task_columns
        assert "fallback_to_agent" in run_columns
        with make_session_factory(engine)() as session:
            assert SettingsRepository(session).get("schema_version") == SCHEMA_VERSION
        engine.dispose()

    def test_schema_v3_is_migrated_with_recurring_task_columns(self, tmp_path):
        db_path = tmp_path / "v3.db"
        engine = create_db_engine(db_path)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "CREATE TABLE tasks (id INTEGER PRIMARY KEY, fallback_agent VARCHAR(6))"
            )
            connection.exec_driver_sql("INSERT INTO tasks (id) VALUES (1)")
            connection.exec_driver_sql(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json JSON NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value_json) VALUES ('schema_version', '3')"
            )

        init_db(engine)

        task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
        task_indexes = {
            index["name"]: index for index in inspect(engine).get_indexes("tasks")
        }
        assert "recurrence_interval_days" in task_columns
        assert "recurrence_source_task_id" in task_columns
        assert task_indexes["uq_tasks_recurrence_source_task_id"]["unique"]
        with engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT recurrence_interval_days, recurrence_source_task_id "
                "FROM tasks WHERE id = 1"
            ).one() == (None, None)
        with make_session_factory(engine)() as session:
            assert SettingsRepository(session).get("schema_version") == SCHEMA_VERSION
        engine.dispose()

    def test_schema_v4_is_migrated_with_worktree_columns(self, tmp_path):
        db_path = tmp_path / "v4.db"
        engine = create_db_engine(db_path)
        with engine.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE tasks (id INTEGER PRIMARY KEY)")
            connection.exec_driver_sql(
                "CREATE TABLE task_runs (id INTEGER PRIMARY KEY, task_id INTEGER NOT NULL)"
            )
            connection.exec_driver_sql(
                "CREATE TABLE settings (key TEXT PRIMARY KEY, value_json JSON NOT NULL)"
            )
            connection.exec_driver_sql(
                "INSERT INTO settings (key, value_json) VALUES ('schema_version', '4')"
            )

        init_db(engine)

        task_columns = {column["name"] for column in inspect(engine).get_columns("tasks")}
        run_columns = {
            column["name"] for column in inspect(engine).get_columns("task_runs")
        }
        assert {"use_worktree", "worktree_path", "worktree_branch"} <= task_columns
        assert {"worktree_path", "worktree_branch"} <= run_columns
        with make_session_factory(engine)() as session:
            assert SettingsRepository(session).get("schema_version") == SCHEMA_VERSION
        engine.dispose()


class TestDependencies:
    def test_dependency_roundtrip(self, session):
        repo = TaskRepository(session)
        first = repo.add(make_task(title="schema"))
        second = repo.add(make_task(title="repository"))
        repo.add_dependency(second, first)
        session.commit()

        loaded = repo.get(second.id)
        assert [t.title for t in loaded.depends_on] == ["schema"]
        assert [t.title for t in first.dependents] == ["repository"]

    def test_self_dependency_rejected(self, session):
        repo = TaskRepository(session)
        task = repo.add(make_task())
        with pytest.raises(ValueError):
            repo.add_dependency(task, task)


class TestAgentState:
    def test_get_or_create_defaults_to_unknown(self, session):
        repo = AgentStateRepository(session)
        state = repo.get_or_create(AgentName.CLAUDE)
        session.commit()
        assert state.status is AgentStatus.UNKNOWN
        assert repo.get_or_create(AgentName.CLAUDE) is state  # no duplicate row

    def test_current_task_fk_enforced(self, session):
        state = AgentStateRepository(session).get_or_create(AgentName.CODEX)
        state.current_task_id = 9999  # no such task
        with pytest.raises(IntegrityError):
            session.commit()


class TestTaskRuns:
    def test_run_roundtrip(self, session):
        task = TaskRepository(session).add(make_task())
        runs = TaskRunRepository(session)
        runs.add(
            TaskRun(
                task_id=task.id,
                agent=AgentName.CLAUDE,
                attempt=1,
                command=["claude", "-p", "hello"],
                exit_code=1,
                error_type=ErrorType.USAGE_LIMIT,
            )
        )
        session.commit()

        loaded = runs.list_for_task(task.id)
        assert len(loaded) == 1
        assert loaded[0].command == ["claude", "-p", "hello"]
        assert loaded[0].error_type is ErrorType.USAGE_LIMIT
        assert task.runs == loaded


class TestSettings:
    def test_set_and_overwrite(self, session):
        repo = SettingsRepository(session)
        repo.set("ui.theme", {"dark": True})
        session.commit()
        assert repo.get("ui.theme") == {"dark": True}

        repo.set("ui.theme", {"dark": False})
        session.commit()
        assert repo.get("ui.theme") == {"dark": False}
        assert repo.get("missing", default=42) == 42
