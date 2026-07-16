"""Adapter for Codex CLI 0.144.x non-interactive execution."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.adapters.base import (
    classify_cli_error,
    executable_is_installed,
    read_version,
    render_prompt,
    validated_limit_reset,
)

_AUTH_PATTERNS = (
    "unauthorized",
    "not authenticated",
    "authentication failed",
    "openai_api_key",
    "invalid api key",
    "please log in",
)

_ABSOLUTE_RESET_RE = re.compile(
    r"\b(?:try\s+again|resets?)\s+at\s+"
    r"(?P<timestamp>"
    r"\d{10}(?:\.\d+)?|"
    r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?"
    r"(?:Z|[+-]\d{2}:\d{2}|\s+UTC)"
    r")\b",
    re.IGNORECASE,
)
_RELATIVE_RESET_RE = re.compile(
    r"\btry\s+again\s+in\s+"
    r"(?:(?P<hours>\d{1,3})\s*(?:h|hours?))?"
    r"(?:\s*(?:and\s+)?(?P<minutes>\d{1,4})\s*(?:m|mins?|minutes?))?\b",
    re.IGNORECASE,
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

    def extract_limit_reset(self, output: str) -> datetime | None:
        now = utcnow()
        for match in _ABSOLUTE_RESET_RE.finditer(output):
            reset_at = _parse_absolute_reset(match.group("timestamp"))
            if validated := validated_limit_reset(reset_at, now=now):
                return validated
        for match in _RELATIVE_RESET_RE.finditer(output):
            hours = int(match.group("hours") or 0)
            minutes = int(match.group("minutes") or 0)
            if not hours and not minutes:
                continue
            if validated := validated_limit_reset(
                now + timedelta(hours=hours, minutes=minutes),
                now=now,
            ):
                return validated
        return None

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        return classify_cli_error(output, exit_code, self._settings, _AUTH_PATTERNS)


def _parse_absolute_reset(value: str) -> datetime | None:
    try:
        if re.fullmatch(r"\d{10}(?:\.\d+)?", value):
            return datetime.fromtimestamp(float(value), UTC).replace(tzinfo=None)
        normalized = value.strip()
        if normalized.casefold().endswith(" utc"):
            normalized = f"{normalized[:-4]}+00:00"
        elif normalized.casefold().endswith("z"):
            normalized = f"{normalized[:-1]}+00:00"
        parsed = datetime.fromisoformat(normalized)
    except (OSError, OverflowError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC).replace(tzinfo=None)
