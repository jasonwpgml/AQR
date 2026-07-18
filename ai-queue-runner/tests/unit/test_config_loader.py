from pathlib import Path

import pytest

from ai_queue_runner.config.loader import ConfigError, default_app_data_dir, load_config


def test_defaults_when_no_config_file(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    config = load_config()
    assert config.app.global_auto_run is False
    assert config.app.db_path == tmp_path / "local" / "AIQueueRunner" / "data/queue.db"
    assert config.app.logs_dir == tmp_path / "local" / "AIQueueRunner" / "logs"
    assert config.app.worktrees_dir == tmp_path / "local" / "AIQueueRunner" / "worktrees"
    assert set(config.agents) == {"claude", "codex"}
    assert config.scheduler.fallback_enabled is True
    assert config.scheduler.fallback_failure_threshold == 1
    assert config.scheduler.worktree_parallel_enabled is False
    assert config.scheduler.auto_commit_enabled is False
    assert config.scheduler.draft_pr_enabled is False


def test_default_app_data_dir_has_home_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert default_app_data_dir() == tmp_path / ".local/share/ai-queue-runner"


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
fallback_enabled = false
fallback_failure_threshold = 2
worktree_parallel_enabled = true
auto_commit_enabled = true
auto_commit_git_timeout_seconds = 45
draft_pr_enabled = true
draft_pr_command_timeout_seconds = 50

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
    assert config.scheduler.fallback_enabled is False
    assert config.scheduler.fallback_failure_threshold == 2
    assert config.scheduler.worktree_parallel_enabled is True
    assert config.scheduler.auto_commit_enabled is True
    assert config.scheduler.auto_commit_git_timeout_seconds == 45
    assert config.scheduler.draft_pr_enabled is True
    assert config.scheduler.draft_pr_command_timeout_seconds == 50
    assert config.agents["claude"].limit_patterns == ["custom limit message"]
    assert config.agents["claude"].executable == "claude"
    assert config.agents["codex"].executable == "codex"
    # unspecified sections keep defaults
    assert config.scheduler.retry_penalty_per_attempt == 5


def test_relative_paths_resolve_against_config_dir(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(
        '[app]\ndb_path = "data/queue.db"\nlogs_dir = "logs"\n'
        'worktrees_dir = "worktrees"\n',
        encoding="utf-8",
    )
    config = load_config(path)
    assert config.app.db_path == tmp_path / "data/queue.db"
    assert config.app.logs_dir == tmp_path / "logs"
    assert config.app.worktrees_dir == tmp_path / "worktrees"


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


def test_unknown_agent_table_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text("[agents.claudee]\nenabled = true\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="unsupported agent"):
        load_config(path)


def test_invalid_value_rejected(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[app]\nlog_level = "VERBOSE"\n', encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid configuration"):
        load_config(path)
