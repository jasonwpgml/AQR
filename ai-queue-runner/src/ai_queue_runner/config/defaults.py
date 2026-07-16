"""Configuration models and default values.

Every tunable named in AI_QUEUE_RUNNER_PLAN.md (scoring bonuses, dedicated-task
protection, limit-detection patterns) lives here so it can be overridden from
config.toml instead of being hardcoded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_CONFIG_FILENAME = "config.toml"

# AIDEV-NOTE: limit_patterns are matched against real CLI output in Phase 4; these
# defaults are placeholders and must stay user-editable (plan §7.2 [RISK]).
DEFAULT_LIMIT_PATTERNS = [
    "usage limit",
    "rate limit",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AppSection(StrictModel):
    global_auto_run: bool = False
    allowed_project_roots: list[Path] = Field(default_factory=list)
    db_path: Path = Path("data/queue.db")
    logs_dir: Path = Path("logs")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"


class SchedulerSection(StrictModel):
    poll_interval_seconds: int = Field(default=30, ge=1)
    dedicated_agent_bonus: int = Field(default=20, ge=0)
    waiting_time_bonus_per_hour: float = Field(default=1, ge=0)
    waiting_time_bonus_max_hours: int = Field(default=24, ge=0)
    retry_penalty_per_attempt: int = Field(default=5, ge=0)
    dependency_bonus_per_dependent: int = Field(default=2, ge=0)
    dependency_bonus_max: int = Field(default=10, ge=0)
    dedicated_wait_hours_threshold: int = Field(default=12, ge=0)
    dedicated_wait_extra_bonus: int = Field(default=10, ge=0)
    dedicated_priority_preempt: int = Field(default=90, ge=0, le=100)
    reserved_timeout_minutes: int = Field(default=10, ge=1)
    # AIDEV-NOTE: only "exclusive" is implemented in v1 (plan §5.6); widen the
    # Literal when git_branch/none modes land.
    project_lock_mode: Literal["exclusive"] = "exclusive"


class AgentSection(StrictModel):
    enabled: bool = True
    run_timeout_minutes: int = Field(default=120, ge=1)
    limit_patterns: list[str] = Field(default_factory=lambda: list(DEFAULT_LIMIT_PATTERNS))


def default_agents() -> dict[str, AgentSection]:
    return {"claude": AgentSection(), "codex": AgentSection()}


class AppConfig(StrictModel):
    app: AppSection = Field(default_factory=AppSection)
    scheduler: SchedulerSection = Field(default_factory=SchedulerSection)
    agents: dict[str, AgentSection] = Field(default_factory=default_agents)
