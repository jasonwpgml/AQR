"""Settings-backed prompt template management."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy.orm import Session

from ai_queue_runner.infrastructure.repositories import SettingsRepository

SessionFactory = Callable[[], Session]

_SETTINGS_KEY = "prompt_templates"
_MAX_NAME_LENGTH = 60
_MAX_TEMPLATE_COUNT = 200


@dataclass(frozen=True)
class TemplateView:
    name: str
    prompt: str


class PromptTemplateService:
    """Persist named prompt text without coupling templates to tasks."""

    def __init__(self, session_factory: SessionFactory) -> None:
        self._session_factory = session_factory

    def list(self) -> list[TemplateView]:
        with self._session_factory() as session:
            templates = self._load(SettingsRepository(session))
            return sorted(templates, key=lambda template: template.name)

    def save(self, name: str, prompt: str) -> TemplateView:
        normalized_name = _normalize_name(name)
        normalized_prompt = _normalize_prompt(prompt)
        saved = TemplateView(normalized_name, normalized_prompt)
        with self._session_factory() as session:
            repo = SettingsRepository(session)
            templates = self._load(repo)
            by_name = {template.name: template for template in templates}
            if normalized_name not in by_name and len(templates) >= _MAX_TEMPLATE_COUNT:
                raise ValueError(f"prompt template count cannot exceed {_MAX_TEMPLATE_COUNT}")
            by_name[normalized_name] = saved
            self._store(repo, by_name.values())
            session.commit()
        return saved

    def rename(self, old: str, new: str) -> TemplateView:
        old_name = _normalize_name(old)
        new_name = _normalize_name(new)
        with self._session_factory() as session:
            repo = SettingsRepository(session)
            templates = self._load(repo)
            by_name = {template.name: template for template in templates}
            existing = by_name.get(old_name)
            if existing is None:
                raise KeyError(f"prompt template not found: {old_name}")
            if new_name != old_name and new_name in by_name:
                raise ValueError(f"prompt template already exists: {new_name}")
            renamed = TemplateView(new_name, existing.prompt)
            if new_name != old_name:
                del by_name[old_name]
                by_name[new_name] = renamed
                self._store(repo, by_name.values())
                session.commit()
            return renamed

    def delete(self, name: str) -> None:
        normalized_name = _normalize_name(name)
        with self._session_factory() as session:
            repo = SettingsRepository(session)
            templates = self._load(repo)
            by_name = {template.name: template for template in templates}
            if normalized_name not in by_name:
                raise KeyError(f"prompt template not found: {normalized_name}")
            del by_name[normalized_name]
            self._store(repo, by_name.values())
            session.commit()

    @staticmethod
    def _load(repo: SettingsRepository) -> list[TemplateView]:
        raw_templates = repo.get(_SETTINGS_KEY, default=[])
        if not isinstance(raw_templates, list):
            raise ValueError("stored prompt templates must be a list")
        templates: list[TemplateView] = []
        names: set[str] = set()
        for raw_template in raw_templates:
            if not isinstance(raw_template, dict):
                raise ValueError("stored prompt template must be an object")
            name = raw_template.get("name")
            prompt = raw_template.get("prompt")
            if not isinstance(name, str) or not isinstance(prompt, str):
                raise ValueError("stored prompt template name and prompt must be strings")
            normalized_name = _normalize_name(name)
            if normalized_name in names:
                raise ValueError(f"stored prompt template name is duplicated: {normalized_name}")
            names.add(normalized_name)
            templates.append(TemplateView(normalized_name, _normalize_prompt(prompt)))
        if len(templates) > _MAX_TEMPLATE_COUNT:
            raise ValueError(f"stored prompt template count exceeds {_MAX_TEMPLATE_COUNT}")
        return templates

    @staticmethod
    def _store(repo: SettingsRepository, templates) -> None:
        repo.set(
            _SETTINGS_KEY,
            [
                {"name": template.name, "prompt": template.prompt}
                for template in sorted(templates, key=lambda template: template.name)
            ],
        )


def _normalize_name(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("prompt template name must be a string")
    normalized = name.strip()
    if not normalized:
        raise ValueError("prompt template name must not be empty")
    if len(normalized) > _MAX_NAME_LENGTH:
        raise ValueError(f"prompt template name cannot exceed {_MAX_NAME_LENGTH} characters")
    return normalized


def _normalize_prompt(prompt: str) -> str:
    if not isinstance(prompt, str):
        raise TypeError("prompt template prompt must be a string")
    normalized = prompt.strip()
    if not normalized:
        raise ValueError("prompt template prompt must not be empty")
    return normalized
