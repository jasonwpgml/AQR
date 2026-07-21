"""TOML configuration loading and validation."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path

from pydantic import ValidationError

from ai_queue_runner.config.defaults import DEFAULT_CONFIG_FILENAME, AppConfig


class ConfigError(Exception):
    """Raised when the config file cannot be read or fails validation."""


def load_config(path: Path | None = None) -> AppConfig:
    """Load configuration from a TOML file.

    With no explicit path, looks for config.toml in the current directory and
    silently falls back to defaults when absent. An explicit path that does not
    exist is an error, so a typo in --config never runs with defaults.
    """
    explicit = path is not None
    if path is None:
        path = Path.cwd() / DEFAULT_CONFIG_FILENAME

    if not path.exists():
        if explicit:
            raise ConfigError(f"config file not found: {path}")
        config = AppConfig()
        _resolve_app_paths(config, default_app_data_dir())
        return config

    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        config = AppConfig.model_validate(_merge_agent_defaults(data))
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{exc}") from exc

    # AIDEV-NOTE: relative DB/log/worktree paths resolve against the config file's
    # directory, not the process cwd, so launching from anywhere is safe.
    _resolve_app_paths(config, path.resolve().parent)
    return config


def _resolve(base: Path, value: Path) -> Path:
    return value if value.is_absolute() else base / value


def _resolve_app_paths(config: AppConfig, base: Path) -> None:
    config.app.db_path = _resolve(base, config.app.db_path)
    config.app.logs_dir = _resolve(base, config.app.logs_dir)
    config.app.worktrees_dir = _resolve(base, config.app.worktrees_dir)


def default_app_data_dir() -> Path:
    """Return a stable per-user writable root for implicit configuration."""
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "AIQueueRunner"
    return Path.home() / ".local" / "share" / "ai-queue-runner"


def _merge_agent_defaults(data: dict[str, object]) -> dict[str, object]:
    """Preserve both built-in agents when TOML overrides only one agent table."""
    configured = data.get("agents")
    if configured is None or not isinstance(configured, dict):
        return data

    defaults = AppConfig().model_dump(mode="python")["agents"]
    unknown = sorted(set(configured) - set(defaults))
    if unknown:
        raise ConfigError(f"unsupported agent configuration: {', '.join(unknown)}")
    merged = dict(defaults)
    for name, overrides in configured.items():
        base = merged.get(name)
        if isinstance(base, dict) and isinstance(overrides, dict):
            merged[name] = {**base, **overrides}
        else:
            merged[name] = overrides
    return {**data, "agents": merged}
