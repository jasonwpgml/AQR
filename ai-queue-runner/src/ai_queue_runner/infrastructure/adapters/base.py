"""Common interface implemented by CLI-specific adapters."""

from __future__ import annotations

from typing import Protocol

from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task


class AgentAdapter(Protocol):
    """Build an agent command and classify its completed output."""

    name: AgentName

    def is_installed(self) -> bool: ...

    def build_command(self, task: Task) -> list[str]: ...

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType: ...
