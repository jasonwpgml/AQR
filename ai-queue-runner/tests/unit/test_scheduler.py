from datetime import datetime, timedelta

import pytest

from ai_queue_runner.application.scheduler_service import SchedulerService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    TaskStatus,
)
from ai_queue_runner.domain.models import AgentState, Task

NOW = datetime(2026, 7, 16, 1, 0, 0)


def make_task(task_id: int, **overrides) -> Task:
    defaults = dict(
        id=task_id,
        title=f"task {task_id}",
        prompt="do something",
        project_path=f"C:/projects/{task_id}",
        agent_policy=AgentPolicy.ANY,
        priority=50,
        status=TaskStatus.WAITING,
        max_retries=2,
        retry_count=0,
        created_at=NOW,
    )
    defaults.update(overrides)
    return Task(**defaults)


def available_state(agent: AgentName) -> AgentState:
    return AgentState(agent=agent, status=AgentStatus.AVAILABLE)


@pytest.mark.parametrize("agent", [AgentName.CLAUDE, AgentName.CODEX])
def test_any_task_is_candidate_for_both_agents(agent):
    task = make_task(1, agent_policy=AgentPolicy.ANY)

    selected = SchedulerService(AppConfig()).select_task(
        [task], agent, available_state(agent), NOW
    )

    assert selected is task


@pytest.mark.parametrize(
    ("policy", "agent"),
    [
        (AgentPolicy.CLAUDE, AgentName.CODEX),
        (AgentPolicy.CODEX, AgentName.CLAUDE),
    ],
)
def test_dedicated_task_is_not_selected_by_other_agent(policy, agent):
    task = make_task(1, agent_policy=policy)

    selected = SchedulerService(AppConfig()).select_task(
        [task], agent, available_state(agent), NOW
    )

    assert selected is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": TaskStatus.CANCELLED},
        {"not_before": NOW + timedelta(seconds=1)},
        {"retry_count": 3, "max_retries": 2},
    ],
)
def test_ineligible_task_is_filtered(overrides):
    task = make_task(1, **overrides)

    selected = SchedulerService(AppConfig()).select_task(
        [task], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    )

    assert selected is None


def test_task_waits_until_all_dependencies_succeed():
    pending = make_task(1)
    succeeded = make_task(2, status=TaskStatus.SUCCEEDED)
    blocked = make_task(3)
    blocked.depends_on.extend([pending, succeeded])
    ready = make_task(4)
    ready.depends_on.append(succeeded)

    service = SchedulerService(AppConfig())
    assert service.select_task(
        [blocked], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    ) is None
    assert service.select_task(
        [ready], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    ) is ready


@pytest.mark.parametrize("lock_status", [TaskStatus.RESERVED, TaskStatus.RUNNING])
def test_reserved_or_running_task_locks_its_project(lock_status):
    candidate = make_task(1, project_path="C:/projects/shared")
    lock = make_task(2, project_path="C:/projects/shared", status=lock_status)

    selected = SchedulerService(AppConfig()).select_task(
        [candidate, lock], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    )

    assert selected is None


@pytest.mark.parametrize(
    "state",
    [
        None,
        AgentState(agent=AgentName.CLAUDE, status=AgentStatus.DISABLED),
        AgentState(agent=AgentName.CLAUDE, status=AgentStatus.BUSY),
        AgentState(
            agent=AgentName.CLAUDE,
            status=AgentStatus.AVAILABLE,
            available_at=NOW + timedelta(minutes=1),
        ),
        AgentState(
            agent=AgentName.CLAUDE,
            status=AgentStatus.AVAILABLE,
            current_task_id=99,
        ),
    ],
)
def test_unavailable_agent_selects_nothing(state):
    selected = SchedulerService(AppConfig()).select_task(
        [make_task(1)], AgentName.CLAUDE, state, NOW
    )

    assert selected is None


def test_disabled_agent_config_selects_nothing():
    config = AppConfig()
    config.agents["claude"].enabled = False

    selected = SchedulerService(config).select_task(
        [make_task(1)], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    )

    assert selected is None


def test_high_priority_dedicated_task_preempts_any_task():
    dedicated = make_task(
        1,
        agent_policy=AgentPolicy.CLAUDE,
        priority=90,
        created_at=NOW,
    )
    any_task = make_task(2, priority=100, created_at=NOW - timedelta(hours=24))

    selected = SchedulerService(AppConfig()).select_task(
        [any_task, dedicated], AgentName.CLAUDE, available_state(AgentName.CLAUDE), NOW
    )

    assert selected is dedicated


def test_ties_break_by_dedication_priority_wait_created_at_then_id():
    service = SchedulerService(AppConfig())
    state = available_state(AgentName.CLAUDE)

    dedicated = make_task(1, agent_policy=AgentPolicy.CLAUDE, priority=30)
    any_task = make_task(2, agent_policy=AgentPolicy.ANY, priority=50)
    assert service.select_task([any_task, dedicated], AgentName.CLAUDE, state, NOW) is dedicated

    high_priority = make_task(3, priority=50)
    low_priority_with_wait = make_task(
        4,
        priority=49,
        created_at=NOW - timedelta(hours=1),
    )
    assert (
        service.select_task([low_priority_with_wait, high_priority], AgentName.CLAUDE, state, NOW)
        is high_priority
    )

    config = AppConfig()
    config.scheduler.waiting_time_bonus_per_hour = 0
    no_wait_bonus = SchedulerService(config)
    created_at = NOW - timedelta(hours=3)
    waited_longer = make_task(5, not_before=NOW - timedelta(hours=2), created_at=created_at)
    waited_less = make_task(6, not_before=NOW - timedelta(hours=1), created_at=created_at)
    assert (
        no_wait_bonus.select_task([waited_less, waited_longer], AgentName.CLAUDE, state, NOW)
        is waited_longer
    )

    eligible_at = NOW - timedelta(minutes=30)
    older = make_task(7, created_at=NOW - timedelta(hours=2), not_before=eligible_at)
    newer = make_task(8, created_at=NOW - timedelta(hours=1), not_before=eligible_at)
    assert no_wait_bonus.select_task([newer, older], AgentName.CLAUDE, state, NOW) is older

    low_id = make_task(9)
    high_id = make_task(10)
    assert service.select_task([high_id, low_id], AgentName.CLAUDE, state, NOW) is low_id
