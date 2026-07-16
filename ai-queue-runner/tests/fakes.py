"""Reusable test doubles for integration flows."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field

from ai_queue_runner.domain.enums import AgentName, ErrorType
from ai_queue_runner.domain.models import Task


@dataclass
class FakeAdapter:
    """Configurable adapter that defaults to a harmless Python subprocess."""

    name: AgentName = AgentName.CLAUDE
    command: list[str] = field(
        default_factory=lambda: [sys.executable, "-c", "print('fake adapter completed')"]
    )
    installed: bool = True
    error_patterns: dict[str, ErrorType] = field(
        default_factory=lambda: {
            "auth": ErrorType.AUTH,
            "crash": ErrorType.CRASH,
        }
    )

    def is_installed(self) -> bool:
        return self.installed

    def build_command(self, task: Task) -> list[str]:
        return list(self.command)

    def classify_error(self, output: str, exit_code: int | None) -> ErrorType:
        folded = output.casefold()
        return next(
            (
                error_type
                for pattern, error_type in self.error_patterns.items()
                if pattern.casefold() in folded
            ),
            ErrorType.UNKNOWN,
        )
