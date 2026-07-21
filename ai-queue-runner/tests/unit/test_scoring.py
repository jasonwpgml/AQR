from datetime import datetime, timedelta

import pytest

from ai_queue_runner.config.defaults import SchedulerSection
from ai_queue_runner.domain.enums import AgentName, AgentPolicy
from ai_queue_runner.domain.models import Task
from ai_queue_runner.domain.scoring import calculate_score

NOW = datetime(2026, 7, 16, 1, 0, 0)


def make_task(**overrides) -> Task:
    defaults = dict(
        title="score me",
        prompt="do something",
        project_path="C:/projects/demo",
        agent_policy=AgentPolicy.ANY,
        priority=50,
        retry_count=0,
        created_at=NOW,
    )
    defaults.update(overrides)
    return Task(**defaults)


def test_policy_mismatch_has_no_score():
    task = make_task(agent_policy=AgentPolicy.CLAUDE)

    assert calculate_score(task, AgentName.CODEX, NOW, SchedulerSection()) is None


def test_score_applies_dedicated_waiting_retry_and_dependency_weights():
    task = make_task(
        agent_policy=AgentPolicy.CLAUDE,
        priority=70,
        retry_count=2,
        created_at=NOW - timedelta(hours=3),
    )

    score = calculate_score(
        task,
        AgentName.CLAUDE,
        NOW,
        SchedulerSection(),
        blocked_dependents_count=2,
    )

    assert score == pytest.approx(87)  # 70 + 20 + 3 - 10 + 4


def test_waiting_and_dependency_bonuses_are_capped():
    task = make_task(created_at=NOW - timedelta(hours=100))

    score = calculate_score(
        task,
        AgentName.CLAUDE,
        NOW,
        SchedulerSection(),
        blocked_dependents_count=100,
    )

    assert score == pytest.approx(84)  # priority 50 + waiting 24 + dependencies 10


def test_dedicated_long_wait_extra_bonus_is_configurable():
    task = make_task(
        agent_policy=AgentPolicy.CODEX,
        created_at=NOW - timedelta(hours=12),
    )
    settings = SchedulerSection(
        dedicated_agent_bonus=7,
        waiting_time_bonus_per_hour=0.5,
        dedicated_wait_extra_bonus=11,
    )

    score = calculate_score(task, AgentName.CODEX, NOW, settings)

    assert score == pytest.approx(74)  # 50 + 7 + 6 + 11


def test_dependency_bonus_rate_is_configurable():
    task = make_task()
    settings = SchedulerSection(dependency_bonus_per_dependent=3, dependency_bonus_max=8)

    score = calculate_score(
        task,
        AgentName.CLAUDE,
        NOW,
        settings,
        blocked_dependents_count=3,
    )

    assert score == pytest.approx(58)
