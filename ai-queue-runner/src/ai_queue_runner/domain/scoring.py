"""Pure task-scoring rules from plan §5.2 and §5.5."""

from __future__ import annotations

from datetime import datetime

from ai_queue_runner.config.defaults import SchedulerSection
from ai_queue_runner.domain.enums import AgentName, AgentPolicy
from ai_queue_runner.domain.models import Task


def calculate_score(
    task: Task,
    agent: AgentName,
    now: datetime,
    settings: SchedulerSection,
    *,
    blocked_dependents_count: int = 0,
) -> float | None:
    """Return a candidate's score, or ``None`` when its policy rejects the agent."""
    policy = AgentPolicy(task.agent_policy)
    dedicated_policy = AgentPolicy(agent.value)
    if policy not in (dedicated_policy, AgentPolicy.ANY):
        return None

    waiting_hours = max(0.0, (now - task.created_at).total_seconds() / 3600)
    capped_waiting_hours = min(waiting_hours, settings.waiting_time_bonus_max_hours)
    score = float(task.priority)
    score += capped_waiting_hours * settings.waiting_time_bonus_per_hour
    score -= task.retry_count * settings.retry_penalty_per_attempt
    score += min(
        max(0, blocked_dependents_count) * settings.dependency_bonus_per_dependent,
        settings.dependency_bonus_max,
    )

    if policy is dedicated_policy:
        score += settings.dedicated_agent_bonus
        if waiting_hours >= settings.dedicated_wait_hours_threshold:
            score += settings.dedicated_wait_extra_bonus

    return score
