"""All domain enums in one place.

Values are stored as-is in SQLite TEXT columns, so renaming a member value is a
schema migration, not a refactor.
"""

from __future__ import annotations

from enum import StrEnum


class AgentName(StrEnum):
    CLAUDE = "claude"
    CODEX = "codex"


class AgentPolicy(StrEnum):
    """Which agent a task may run on. ANY means either agent can take it."""

    CLAUDE = "claude"
    CODEX = "codex"
    ANY = "any"


class TaskStatus(StrEnum):
    WAITING = "waiting"
    RESERVED = "reserved"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRY_WAIT = "retry_wait"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AgentStatus(StrEnum):
    AVAILABLE = "available"
    BUSY = "busy"
    COOLDOWN = "cooldown"
    DISABLED = "disabled"
    ERROR = "error"
    UNKNOWN = "unknown"


class ApprovalMode(StrEnum):
    AUTO = "auto"
    CONFIRM = "confirm"


class ReviewVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"


class ErrorType(StrEnum):
    """Classification of a failed task run (plan §6.1 classify_error)."""

    USAGE_LIMIT = "usage_limit"
    AUTH = "auth"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    CRASH = "crash"
    UNKNOWN = "unknown"
