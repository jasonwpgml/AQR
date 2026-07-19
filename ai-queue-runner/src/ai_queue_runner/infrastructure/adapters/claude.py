"""Adapter for Claude Code 2.1.x non-interactive execution."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.adapters.base import (
    classify_cli_error,
    read_version,
    render_prompt,
    resolve_executable,
    validated_limit_reset,
)

_AUTH_PATTERNS = (
    "not logged in",
    "run /login",
    "authentication failed",
    "unauthorized",
    "invalid api key",
)
_NON_BLOCKING_LIMIT_STATUSES = frozenset({"allowed", "allowed_warning"})
_BLOCKING_LIMIT_STATUSES = frozenset({"blocked", "denied", "exceeded", "rejected"})


class ClaudeAdapter:
    name = AgentName.CLAUDE

    def __init__(
        self,
        settings: AgentSection | None = None,
        *,
        executable: str | None = None,
    ) -> None:
        self._settings = settings or AgentSection()
        explicit = executable if executable is not None else self._settings.executable
        self._executable, self._executable_found = resolve_executable("claude", explicit)

    def is_installed(self) -> bool:
        return self._settings.enabled and self._executable_found

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

    def summarize_output(self, output: str) -> str | None:
        summary = None
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except Exception:
                continue
            if (
                isinstance(event, dict)
                and event.get("type") == "result"
                and isinstance(event.get("result"), str)
                and event["result"].strip()
            ):
                summary = event["result"].strip()
        return summary[:500] if summary else None

    def extract_limit_reset(self, output: str) -> datetime | None:
        for rate_limit_info in _rate_limit_infos(output):
            if _limit_status(rate_limit_info) not in _BLOCKING_LIMIT_STATUSES:
                continue
            if validated := _reset_at(rate_limit_info):
                return validated
        return None

    def extract_limit_warning_reset(self, output: str) -> datetime | None:
        for rate_limit_info in _rate_limit_infos(output):
            status = _limit_status(rate_limit_info)
            if not (
                status == "allowed_warning"
                or (
                    status in _NON_BLOCKING_LIMIT_STATUSES
                    and rate_limit_info.get("surpassedThreshold") is True
                )
            ):
                continue
            if validated := _reset_at(rate_limit_info):
                return validated
        return None

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        filtered_output, blocked = _filter_rate_limit_events(output)
        if blocked:
            return ErrorType.USAGE_LIMIT
        return classify_cli_error(
            filtered_output,
            exit_code,
            self._settings,
            _AUTH_PATTERNS,
        )


def _filter_rate_limit_events(output: str) -> tuple[str, bool]:
    filtered_lines: list[str] = []
    blocked = False
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            filtered_lines.append(line)
            continue
        if not isinstance(event, dict) or event.get("type") != "rate_limit_event":
            filtered_lines.append(line)
            continue
        rate_limit_info = event.get("rate_limit_info")
        if isinstance(rate_limit_info, dict):
            blocked = blocked or _limit_status(rate_limit_info) in _BLOCKING_LIMIT_STATUSES
    return "\n".join(filtered_lines), blocked


def _rate_limit_infos(output: str):
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(event, dict) and event.get("type") == "rate_limit_event":
            rate_limit_info = event.get("rate_limit_info")
            if isinstance(rate_limit_info, dict):
                yield rate_limit_info


def _limit_status(rate_limit_info: dict) -> str:
    status = rate_limit_info.get("status")
    return status.casefold() if isinstance(status, str) else ""


def _reset_at(rate_limit_info: dict) -> datetime | None:
    reset_timestamp = rate_limit_info.get("resetsAt")
    if isinstance(reset_timestamp, bool) or not isinstance(reset_timestamp, (int, float)):
        return None
    try:
        reset_at = datetime.fromtimestamp(reset_timestamp, UTC).replace(tzinfo=None)
    except (OSError, OverflowError, ValueError):
        return None
    return validated_limit_reset(reset_at)
