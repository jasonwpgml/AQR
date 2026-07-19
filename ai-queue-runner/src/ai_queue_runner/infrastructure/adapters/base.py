"""Common interface and small helpers for CLI-specific adapters."""

from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import Protocol

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task


class AgentAdapter(Protocol):
    """Build an agent command and classify its completed output."""

    name: AgentName

    def is_installed(self) -> bool: ...

    def get_version(self) -> str | None: ...

    def build_command(self, task: Task) -> list[str]: ...

    def build_stdin(self, task: Task) -> str | None: ...

    def summarize_output(self, output: str) -> str | None: ...

    def extract_limit_reset(self, output: str) -> datetime | None: ...

    def extract_limit_warning_reset(self, output: str) -> datetime | None: ...

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType: ...


def summarize_output(_output: str) -> str | None:
    """Default summary hook for adapters that do not expose one."""
    return None


def extract_limit_reset(_output: str) -> datetime | None:
    """Default reset hook for adapters that do not expose one."""
    return None


def extract_limit_warning_reset(_output: str) -> datetime | None:
    """Default warning-reset hook for adapters without structured warning events."""
    return None


def validated_limit_reset(
    value: datetime | None,
    *,
    now: datetime | None = None,
) -> datetime | None:
    """Return a plausible naive-UTC reset time or reject it."""
    if value is None or value.tzinfo is not None:
        return None
    now = utcnow() if now is None else now
    if value < now or value > now + timedelta(days=7):
        return None
    return value


def resolve_executable(name: str, explicit: str | None = None) -> tuple[str, bool]:
    """Resolve one CLI once, preserving an explicit custom override."""
    expanded = os.path.expanduser(os.path.expandvars(explicit)) if explicit else None
    # AIDEV-NOTE: config defaults use the bare CLI name in the override field; those
    # values must still enter discovery so Explorer-launched builds reach known locations.
    if expanded and expanded != name:
        path = Path(expanded)
        return (str(path.resolve()), True) if path.is_file() else (expanded, False)

    if discovered := shutil.which(name):
        return str(Path(discovered).resolve()), True

    for candidate in _well_known_executable_paths(name):
        if candidate.is_file():
            return str(candidate.resolve()), True
    return name, False


def _well_known_executable_paths(name: str) -> tuple[Path, ...]:
    if name == "claude" and (user_profile := os.environ.get("USERPROFILE")):
        return (Path(user_profile) / ".local" / "bin" / "claude.exe",)
    if name == "codex" and (local_app_data := os.environ.get("LOCALAPPDATA")):
        return (
            Path(local_app_data)
            / "Programs"
            / "OpenAI"
            / "Codex"
            / "bin"
            / "codex.exe",
        )
    return ()


def read_version(executable: str) -> str | None:
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            check=False,
            shell=False,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    output = completed.stdout.strip() or completed.stderr.strip()
    return output or None


def render_prompt(task: Task, template_name: str) -> str:
    template = (
        files("ai_queue_runner.prompts")
        .joinpath(template_name)
        .read_text(encoding="utf-8")
    )
    return template.format(
        task_id=task.id if task.id is not None else "unpersisted",
        project_path=task.project_path,
        prompt=task.prompt,
    )


def classify_cli_error(
    output: str,
    exit_code: int | None,
    settings: AgentSection,
    auth_patterns: tuple[str, ...],
) -> ErrorType:
    if exit_code == 0:
        return ErrorType.UNKNOWN
    folded = output.casefold()
    if any(pattern.casefold() in folded for pattern in settings.limit_patterns):
        return ErrorType.USAGE_LIMIT
    if any(pattern.casefold() in folded for pattern in auth_patterns):
        return ErrorType.AUTH
    if exit_code is None or exit_code < 0:
        return ErrorType.CRASH
    return ErrorType.UNKNOWN
