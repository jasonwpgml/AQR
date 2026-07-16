"""Common interface and small helpers for CLI-specific adapters."""

from __future__ import annotations

import shutil
import subprocess
from importlib.resources import files
from typing import Protocol

from ai_queue_runner.config.defaults import AgentSection
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

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType: ...


def summarize_output(_output: str) -> str | None:
    """Default summary hook for adapters that do not expose one."""
    return None


def executable_is_installed(executable: str) -> bool:
    return shutil.which(executable) is not None


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
