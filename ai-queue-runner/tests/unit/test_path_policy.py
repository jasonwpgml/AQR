from pathlib import Path

import pytest

from ai_queue_runner.infrastructure.path_policy import (
    PathPolicyError,
    validate_project_path,
)


def test_existing_directory_under_allowed_root_is_canonicalized(tmp_path: Path):
    root = tmp_path / "projects"
    project = root / "demo"
    project.mkdir(parents=True)

    assert validate_project_path(project, [root]) == project.resolve()


def test_allowed_root_itself_is_valid(tmp_path: Path):
    assert validate_project_path(tmp_path, [tmp_path]) == tmp_path.resolve()


@pytest.mark.parametrize("roots", [[], [Path("missing-root")]])
def test_empty_or_invalid_allowed_roots_block_execution(tmp_path: Path, roots):
    with pytest.raises(PathPolicyError, match="allowed project root"):
        validate_project_path(tmp_path, roots)


def test_relative_project_path_is_rejected(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("relative").mkdir()

    with pytest.raises(PathPolicyError, match="absolute"):
        validate_project_path("relative", [tmp_path])


def test_missing_or_file_project_path_is_rejected(tmp_path: Path):
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")

    with pytest.raises(PathPolicyError, match="existing directory"):
        validate_project_path(tmp_path / "missing", [tmp_path])
    with pytest.raises(PathPolicyError, match="existing directory"):
        validate_project_path(file_path, [tmp_path])


def test_path_outside_allowed_roots_is_rejected(tmp_path: Path):
    allowed = tmp_path / "allowed"
    outside = tmp_path / "outside"
    allowed.mkdir()
    outside.mkdir()

    with pytest.raises(PathPolicyError, match="outside allowed"):
        validate_project_path(outside, [allowed])
