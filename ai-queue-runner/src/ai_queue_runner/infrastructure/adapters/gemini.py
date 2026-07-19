"""Adapter for Gemini CLI headless JSON execution."""

from __future__ import annotations

import json
import re
from datetime import timedelta

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.clock import utcnow
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
    "not authenticated",
    "authentication failed",
    "unauthorized",
    "invalid api key",
    "please sign in",
    "gemini_api_key",
    "google_api_key",
)
# Official docs establish these flags, but the installed CLI still needs later verification.
_HEADLESS_ARGS = (
    "--prompt",
    "",
    "--output-format",
    "json",
    "--approval-mode",
    "yolo",
    "--sandbox",
)
_RELATIVE_RESET_RE = re.compile(
    r"\b(?:quota\s+will\s+reset|try\s+again)\s+(?:after|in)\s+"
    r"(?:(?P<days>\d{1,2})\s*d(?:ays?)?)?\s*"
    r"(?:(?P<hours>\d{1,3})\s*h(?:ours?)?)?\s*"
    r"(?:(?P<minutes>\d{1,4})\s*m(?:in(?:ute)?s?)?)?\s*"
    r"(?:(?P<seconds>\d{1,4})\s*s(?:ec(?:ond)?s?)?)?\b",
    re.IGNORECASE,
)


class GeminiAdapter:
    name = AgentName.GEMINI

    def __init__(
        self,
        settings: AgentSection | None = None,
        *,
        executable: str | None = None,
    ) -> None:
        self._settings = settings or AgentSection(enabled=False)
        explicit = executable if executable is not None else self._settings.executable
        self._executable, self._executable_found = resolve_executable("gemini", explicit)

    def is_installed(self) -> bool:
        return self._settings.enabled and self._executable_found

    def get_version(self) -> str | None:
        return read_version(self._executable)

    def build_command(self, task: Task) -> list[str]:
        return [self._executable, *_HEADLESS_ARGS]

    def build_stdin(self, task: Task) -> str:
        return render_prompt(task, "gemini_wrapper.txt")

    def summarize_output(self, output: str) -> str | None:
        try:
            payload = json.loads(output)
        except (json.JSONDecodeError, TypeError):
            return None
        response = payload.get("response") if isinstance(payload, dict) else None
        if not isinstance(response, str) or not response.strip():
            return None
        return response.strip()[:500]

    def extract_limit_reset(self, output: str):
        now = utcnow()
        for match in _RELATIVE_RESET_RE.finditer(output):
            values = {
                name: int(match.group(name) or 0)
                for name in ("days", "hours", "minutes", "seconds")
            }
            if not any(values.values()):
                continue
            reset_at = now + timedelta(**values)
            if validated := validated_limit_reset(reset_at, now=now):
                return validated
        return None

    def extract_limit_warning_reset(self, output: str):
        return None

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        return classify_cli_error(output, exit_code, self._settings, _AUTH_PATTERNS)
