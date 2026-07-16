"""Canonical project-path validation shared by task creation and execution."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class PathPolicyError(ValueError):
    """Raised when a project path is unsafe or outside configured roots."""


def validate_project_path(
    project_path: Path | str,
    allowed_roots: Iterable[Path | str],
) -> Path:
    candidate = Path(project_path).expanduser()
    if not candidate.is_absolute():
        raise PathPolicyError(f"project path must be absolute: {candidate}")
    try:
        candidate = candidate.resolve(strict=True)
    except OSError as exc:
        raise PathPolicyError(f"project path must be an existing directory: {candidate}") from exc
    if not candidate.is_dir():
        raise PathPolicyError(f"project path must be an existing directory: {candidate}")

    roots = [_resolve_root(root) for root in allowed_roots]
    valid_roots = [root for root in roots if root is not None]
    if not valid_roots:
        raise PathPolicyError("at least one existing allowed project root is required")
    if not any(candidate == root or root in candidate.parents for root in valid_roots):
        raise PathPolicyError(f"project path is outside allowed roots: {candidate}")
    return candidate


def _resolve_root(value: Path | str) -> Path | None:
    root = Path(value).expanduser()
    if not root.is_absolute():
        return None
    try:
        root = root.resolve(strict=True)
    except OSError:
        return None
    return root if root.is_dir() else None
