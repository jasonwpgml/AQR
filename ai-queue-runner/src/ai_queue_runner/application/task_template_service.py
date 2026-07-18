"""Reusable single-task drafts with explicit title/prompt variable substitution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from string import Formatter

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ai_queue_runner.application.task_service import SessionFactory, TaskDraft
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentPolicy, ApprovalMode
from ai_queue_runner.domain.models import TaskTemplate
from ai_queue_runner.infrastructure.repositories import TaskTemplateRepository


class TaskTemplateDraft(BaseModel):
    """Persistable task fields; project_path may be supplied when instantiating."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    project_path: Path | None = None
    agent_policy: AgentPolicy = AgentPolicy.ANY
    priority: int = Field(default=50, ge=0, le=100)
    estimated_tokens: int = Field(default=0, ge=0)
    approval_mode: ApprovalMode = ApprovalMode.CONFIRM
    max_retries: int = Field(default=2, ge=0)
    retry_delay_minutes: int = Field(default=10, ge=0)
    recurrence_interval_days: int | None = Field(default=None, ge=1)
    not_before: datetime | None = None
    tags: list[str] = Field(default_factory=list)
    dependency_ids: list[int] = Field(default_factory=list)

    @field_validator("not_before")
    @classmethod
    def _require_naive_utc(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is not None:
            raise ValueError("not_before must be a naive UTC datetime")
        return value

    @field_validator("dependency_ids")
    @classmethod
    def _unique_dependency_ids(cls, value: list[int]) -> list[int]:
        if any(task_id <= 0 for task_id in value):
            raise ValueError("dependency IDs must be positive")
        if len(set(value)) != len(value):
            raise ValueError("dependency IDs must be unique")
        return value

    @field_validator("title", "prompt")
    @classmethod
    def _validate_placeholders(cls, value: str) -> str:
        _placeholder_names(value)
        return value


@dataclass(frozen=True)
class TaskTemplateView:
    name: str
    title: str
    prompt: str
    project_path: str | None
    agent_policy: AgentPolicy
    priority: int
    estimated_tokens: int
    approval_mode: ApprovalMode
    max_retries: int
    retry_delay_minutes: int
    recurrence_interval_days: int | None
    not_before: datetime | None
    tags: tuple[str, ...]
    dependency_ids: tuple[int, ...]
    created_at: datetime
    updated_at: datetime


class TaskTemplateService:
    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def save(
        self,
        name: str,
        draft: TaskTemplateDraft,
    ) -> tuple[TaskTemplateView, bool]:
        normalized_name = _normalize_name(name)
        with self._session_factory() as session:
            repo = TaskTemplateRepository(session)
            template = repo.get(normalized_name)
            overwritten = template is not None
            values = _template_values(draft)
            if template is None:
                template = repo.add(TaskTemplate(name=normalized_name, **values))
            else:
                for field, value in values.items():
                    setattr(template, field, value)
                template.updated_at = utcnow()
            session.commit()
            return _to_view(template), overwritten

    def get(self, name: str) -> TaskTemplateView | None:
        with self._session_factory() as session:
            template = TaskTemplateRepository(session).get(_normalize_name(name))
            return None if template is None else _to_view(template)

    def list(self) -> list[TaskTemplateView]:
        with self._session_factory() as session:
            return [_to_view(template) for template in TaskTemplateRepository(session).list()]

    def delete(self, name: str) -> None:
        normalized_name = _normalize_name(name)
        with self._session_factory() as session:
            repo = TaskTemplateRepository(session)
            template = repo.get(normalized_name)
            if template is None:
                raise KeyError(f"template {normalized_name!r} not found")
            repo.delete(template)
            session.commit()

    def render(
        self,
        name: str,
        variables: dict[str, str] | None = None,
        **overrides: object,
    ) -> TaskDraft:
        template = self.get(name)
        if template is None:
            raise KeyError(f"template {_normalize_name(name)!r} not found")
        variables = {} if variables is None else variables
        title = overrides.pop("title", None)
        prompt = overrides.pop("prompt", None)
        template_texts = [
            value
            for value, overridden in (
                (template.title, title is not None),
                (template.prompt, prompt is not None),
            )
            if not overridden
        ]
        missing = sorted(
            set().union(*(_placeholder_names(value) for value in template_texts))
            - variables.keys()
        )
        if missing:
            raise ValueError(f"missing template variables: {', '.join(missing)}")
        rendered_title = title if title is not None else template.title.format_map(variables)
        rendered_prompt = (
            prompt if prompt is not None else template.prompt.format_map(variables)
        )
        unresolved = sorted(
            _unresolved_placeholder_names(str(rendered_title))
            | _unresolved_placeholder_names(str(rendered_prompt))
        )
        if unresolved:
            raise ValueError(f"unresolved template variables: {', '.join(unresolved)}")
        values: dict[str, object] = {
            "title": rendered_title,
            "prompt": rendered_prompt,
            "project_path": template.project_path,
            "agent_policy": template.agent_policy,
            "priority": template.priority,
            "estimated_tokens": template.estimated_tokens,
            "approval_mode": template.approval_mode,
            "max_retries": template.max_retries,
            "retry_delay_minutes": template.retry_delay_minutes,
            "recurrence_interval_days": template.recurrence_interval_days,
            "not_before": template.not_before,
            "tags": list(template.tags),
            "dependency_ids": list(template.dependency_ids),
        }
        unknown = set(overrides) - set(values)
        if unknown:
            raise ValueError(f"unknown template overrides: {', '.join(sorted(unknown))}")
        values.update({key: value for key, value in overrides.items() if value is not None})
        return TaskDraft.model_validate(values)


def _normalize_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("template name must not be empty")
    return normalized


def _placeholder_names(value: str) -> set[str]:
    try:
        parts = list(Formatter().parse(value))
    except ValueError as exc:
        raise ValueError(f"invalid template placeholder syntax: {exc}") from exc
    names = set()
    for _literal, field, format_spec, conversion in parts:
        if field is None:
            continue
        if not field.isidentifier():
            raise ValueError(f"invalid template variable name: {field!r}")
        if format_spec or conversion:
            raise ValueError("template placeholders do not support formatting or conversion")
        names.add(field)
    return names


def _unresolved_placeholder_names(value: str) -> set[str]:
    return {
        match.group(1)
        for match in re.finditer(r"\{([^{}]+)\}", value)
        if match.group(1).isidentifier()
    }


def _template_values(draft: TaskTemplateDraft) -> dict[str, object]:
    return {
        "title": draft.title,
        "prompt": draft.prompt,
        "project_path": None if draft.project_path is None else str(draft.project_path),
        "agent_policy": draft.agent_policy,
        "priority": draft.priority,
        "estimated_tokens": draft.estimated_tokens,
        "approval_mode": draft.approval_mode,
        "max_retries": draft.max_retries,
        "retry_delay_minutes": draft.retry_delay_minutes,
        "recurrence_interval_days": draft.recurrence_interval_days,
        "not_before": draft.not_before,
        "tags": list(draft.tags),
        "dependency_ids": list(draft.dependency_ids),
    }


def _to_view(template: TaskTemplate) -> TaskTemplateView:
    return TaskTemplateView(
        name=template.name,
        title=template.title,
        prompt=template.prompt,
        project_path=template.project_path,
        agent_policy=template.agent_policy,
        priority=template.priority,
        estimated_tokens=template.estimated_tokens,
        approval_mode=template.approval_mode,
        max_retries=template.max_retries,
        retry_delay_minutes=template.retry_delay_minutes,
        recurrence_interval_days=template.recurrence_interval_days,
        not_before=template.not_before,
        tags=tuple(template.tags),
        dependency_ids=tuple(template.dependency_ids),
        created_at=template.created_at,
        updated_at=template.updated_at,
    )
