"""Token-budget-aware deterministic planning for atomic task batches."""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_queue_runner.application.task_service import TaskDraft
from ai_queue_runner.domain.enums import AgentName, AgentPolicy


class TokenBudgets(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claude: int = Field(gt=0)
    codex: int = Field(gt=0)

    def for_agent(self, agent: AgentName) -> int:
        return int(getattr(self, agent.value))


class WorkloadBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budgets: TokenBudgets
    tasks: list[TaskDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_token_estimates(self) -> WorkloadBatch:
        missing = [task.title for task in self.tasks if task.estimated_tokens <= 0]
        if missing:
            raise ValueError(
                "estimated_tokens must be positive for every planned task: "
                + ", ".join(missing)
            )
        return self


@dataclass(frozen=True)
class WorkloadAssignment:
    input_index: int
    requested_policy: AgentPolicy
    assigned_agent: AgentName
    draft: TaskDraft


@dataclass(frozen=True)
class WorkloadPlan:
    budgets: TokenBudgets
    assignments: tuple[WorkloadAssignment, ...]
    claude_tokens: int
    codex_tokens: int

    @property
    def assigned_tokens(self) -> dict[AgentName, int]:
        return {
            AgentName.CLAUDE: self.claude_tokens,
            AgentName.CODEX: self.codex_tokens,
        }

    @property
    def remaining_tokens(self) -> dict[AgentName, int]:
        return {
            agent: self.budgets.for_agent(agent) - used
            for agent, used in self.assigned_tokens.items()
        }


def plan_workload(batch: WorkloadBatch) -> WorkloadPlan:
    """Resolve every ``any`` policy without exceeding either token budget."""
    agents = tuple(AgentName)
    used = {agent: 0 for agent in agents}
    resolved: list[AgentName | None] = [None] * len(batch.tasks)

    for index, draft in enumerate(batch.tasks):
        if draft.agent_policy is AgentPolicy.ANY:
            continue
        agent = AgentName(draft.agent_policy.value)
        projected = used[agent] + draft.estimated_tokens
        if projected > batch.budgets.for_agent(agent):
            raise ValueError(
                f"task '{draft.title}' exceeds the {agent.value} token budget"
            )
        used[agent] = projected
        resolved[index] = agent

    any_indices = sorted(
        (
            index
            for index, draft in enumerate(batch.tasks)
            if draft.agent_policy is AgentPolicy.ANY
        ),
        key=lambda index: (-batch.tasks[index].estimated_tokens, index),
    )
    for index in any_indices:
        draft = batch.tasks[index]
        candidates = [
            agent
            for agent in agents
            if used[agent] + draft.estimated_tokens <= batch.budgets.for_agent(agent)
        ]
        if not candidates:
            raise ValueError(
                f"task '{draft.title}' does not fit remaining token budgets"
            )
        agent = min(
            candidates,
            key=lambda candidate: Fraction(
                used[candidate] + draft.estimated_tokens,
                batch.budgets.for_agent(candidate),
            ),
        )
        used[agent] += draft.estimated_tokens
        resolved[index] = agent

    assignments = tuple(
        WorkloadAssignment(
            input_index=index,
            requested_policy=draft.agent_policy,
            assigned_agent=agent,
            draft=draft.model_copy(
                update={"agent_policy": AgentPolicy(agent.value)}
            ),
        )
        for index, (draft, agent) in enumerate(zip(batch.tasks, resolved, strict=True))
        if agent is not None
    )
    return WorkloadPlan(
        budgets=batch.budgets,
        assignments=assignments,
        claude_tokens=used[AgentName.CLAUDE],
        codex_tokens=used[AgentName.CODEX],
    )
