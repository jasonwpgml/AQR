"""Adapter for Codex CLI 0.144.x non-interactive execution."""

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
    "unauthorized",
    "not authenticated",
    "authentication failed",
    "openai_api_key",
    "invalid api key",
    "please log in",
)


class CodexAdapter:
    name = AgentName.CODEX

    def __init__(
        self,
        settings: AgentSection | None = None,
        *,
        executable: str | None = None,
    ) -> None:
        self._settings = settings or AgentSection()
        self._executable = executable or self._settings.executable or "codex"

    def is_installed(self) -> bool:
        return self._settings.enabled and executable_is_installed(self._executable)

    def get_version(self) -> str | None:
        return read_version(self._executable)

    def build_command(self, task: Task) -> list[str]:
        return [
            self._executable,
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            "workspace-write",
            "--cd",
            task.project_path,
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
            "--json",
            "-",
        ]

    def build_stdin(self, task: Task) -> str:
        return render_prompt(task, "codex_wrapper.txt")

    def summarize_output(self, output: str) -> str | None:
        summary = None
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if not isinstance(event, dict):
                continue
            if event.get("type") == "agent_message":
                message = event.get("text") or event.get("message")
            else:
                item = event.get("item")
                message = (
                    item.get("text") or item.get("message")
                    if event.get("type") == "item.completed"
                    and isinstance(item, dict)
                    and item.get("type") == "agent_message"
                    else None
                )
            if isinstance(message, str) and message.strip():
                summary = message.strip()
        return summary[:500] if summary else None

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        return classify_cli_error(output, exit_code, self._settings, _AUTH_PATTERNS)
