from pathlib import Path

import pytest

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.loader import ConfigError, load_config


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = load_config()
    assert config == AppConfig()
    assert config.app.global_auto_run is False
    assert set(config.agents) == {"claude", "codex"}


def test_explicit_missing_path_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "nope.toml")


def test_loads_values_from_toml(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        """
[app]
global_auto_run = true
allowed_project_roots = ["C:/projects"]
log_level = "DEBUG"

[scheduler]
dedicated_agent_bonus = 30

[agents.claude]
limit_patterns = ["custom limit message"]
""",
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.app.global_auto_run is True
    assert config.app.allowed_project_roots == [Path("C:/projects")]
    assert config.app.log_level == "DEBUG"
    assert config.scheduler.dedicated_agent_bonus == 30
    assert config.agents["claude"].limit_patterns == ["custom limit message"]
    # unspecified sections keep defaults
    assert config.scheduler.retry_penalty_per_attempt == 5


def test_relative_paths_resolve_against_config_dir(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[app]\ndb_path = "data/queue.db"\nlogs_dir = "logs"\n', encoding="utf-8")
    config = load_config(path)
    assert config.app.db_path == tmp_path / "data/queue.db"
    assert config.app.logs_dir == tmp_path / "logs"


def test_invalid_toml_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[app\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(path)


def test_unknown_key_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[app]\nglobal_autorun = true\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(path)


def test_invalid_value_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[app]\nlog_level = "VERBOSE"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(path)
