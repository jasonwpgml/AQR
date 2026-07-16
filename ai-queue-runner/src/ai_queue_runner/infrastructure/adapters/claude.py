"""Adapter for Claude Code 2.1.x non-interactive execution."""

from __future__ import annotations

import json

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.adapters.base import (
    classify_cli_error,
    executable_is_installed,
    read_version,
    render_prompt,
)

_AUTH_PATTERNS = (
    "not logged in",
    "run /login",
    "authentication failed",
    "unauthorized",
    "invalid api key",
)


class ClaudeAdapter:
    name = AgentName.CLAUDE

    def __init__(
        self,
        settings: AgentSection | None = None,
        *,
        executable: str | None = None,
    ) -> None:
        self._settings = settings or AgentSection()
        self._executable = executable or self._settings.executable or "claude"

    def is_installed(self) -> bool:
        return self._settings.enabled and executable_is_installed(self._executable)

    def get_version(self) -> str | None:
        return read_version(self._executable)

    def build_command(self, task: Task) -> list[str]:
        return [
            self._executable,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "auto",
            "--no-session-persistence",
        ]

    def build_stdin(self, task: Task) -> str:
        return render_prompt(task, "claude_wrapper.txt")

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        if _contains_rate_limit_event(output):
            return ErrorType.USAGE_LIMIT
        return classify_cli_error(output, exit_code, self._settings, _AUTH_PATTERNS)


def _contains_rate_limit_event(output: str) -> bool:
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") == "rate_limit_event":
            rate_limit_info = event.get("rate_limit_info")
            if isinstance(rate_limit_info, dict) and rate_limit_info.get("status") == "allowed":
                continue
            return True
    return False
