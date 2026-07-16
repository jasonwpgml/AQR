"""TOML configuration loading and validation."""

from __future__ import annotations

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
        return AppConfig()

    try:
        with path.open("rb") as fp:
            data = tomllib.load(fp)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"cannot read {path}: {exc}") from exc

    try:
        config = AppConfig.model_validate(data)
    except ValidationError as exc:
        raise ConfigError(f"invalid configuration in {path}:\n{exc}") from exc

    # AIDEV-NOTE: relative db_path/logs_dir resolve against the config file's
    # directory, not the process cwd, so launching from anywhere is safe.
    base = path.resolve().parent
    config.app.db_path = _resolve(base, config.app.db_path)
    config.app.logs_dir = _resolve(base, config.app.logs_dir)
    return config


def _resolve(base: Path, value: Path) -> Path:
    return value if value.is_absolute() else base / value
