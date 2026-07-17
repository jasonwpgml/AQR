from pathlib import Path

import pytest
from pydantic import ValidationError

from ai_queue_runner.application.task_service import TaskDraft
from ai_queue_runner.application.workload_planning import (
    TokenBudgets,
    WorkloadBatch,
    plan_workload,
)
from ai_queue_runner.domain.enums import AgentName, AgentPolicy


def planned_task(
    project: Path,
    title: str,
    estimated_tokens: int,
    agent_policy: AgentPolicy = AgentPolicy.ANY,
) -> TaskDraft:
    return TaskDraft(
        title=title,
        prompt=f"Implement {title}",
        project_path=project,
        agent_policy=agent_policy,
        estimated_tokens=estimated_tokens,
    )


def test_plan_workload_balances_any_tasks_deterministically(tmp_path: Path) -> None:
    batch = WorkloadBatch(
        budgets=TokenBudgets(claude=1_000, codex=1_000),
        tasks=[
            planned_task(tmp_path, "claude-only", 300, AgentPolicy.CLAUDE),
            planned_task(tmp_path, "codex-only", 100, AgentPolicy.CODEX),
            planned_task(tmp_path, "large-any", 500),
            planned_task(tmp_path, "medium-any", 300),
            planned_task(tmp_path, "small-any", 200),
        ],
    )

    first = plan_workload(batch)
    second = plan_workload(batch)

    assert first == second
    assert [assignment.assigned_agent for assignment in first.assignments] == [
        AgentName.CLAUDE,
        AgentName.CODEX,
        AgentName.CODEX,
        AgentName.CLAUDE,
        AgentName.CLAUDE,
    ]
    assert [assignment.draft.agent_policy for assignment in first.assignments] == [
        AgentPolicy.CLAUDE,
        AgentPolicy.CODEX,
        AgentPolicy.CODEX,
        AgentPolicy.CLAUDE,
        AgentPolicy.CLAUDE,
    ]
    assert first.assigned_tokens == {
        AgentName.CLAUDE: 800,
        AgentName.CODEX: 600,
    }
    assert first.remaining_tokens == {
        AgentName.CLAUDE: 200,
        AgentName.CODEX: 400,
    }


def test_plan_workload_rejects_missing_estimates_and_capacity_overflow(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValidationError, match="estimated_tokens must be positive"):
        WorkloadBatch(
            budgets=TokenBudgets(claude=100, codex=100),
            tasks=[planned_task(tmp_path, "unknown", 0)],
        )

    batch = WorkloadBatch(
        budgets=TokenBudgets(claude=100, codex=100),
        tasks=[planned_task(tmp_path, "too-large", 201)],
    )
    with pytest.raises(ValueError, match="does not fit remaining token budgets"):
        plan_workload(batch)


def test_plan_workload_rejects_dedicated_task_over_its_agent_budget(
    tmp_path: Path,
) -> None:
    batch = WorkloadBatch(
        budgets=TokenBudgets(claude=100, codex=1_000),
        tasks=[planned_task(tmp_path, "claude-only", 101, AgentPolicy.CLAUDE)],
    )

    with pytest.raises(ValueError, match="claude-only.*claude token budget"):
        plan_workload(batch)
