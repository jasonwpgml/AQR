from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ai_queue_runner.infrastructure.draft_pr import DraftPrPublisher


class FakeCommandRunner:
    def __init__(
        self,
        *,
        existing_url: str | None = None,
        origin_available: bool = True,
        gh_available: bool = True,
        authenticated: bool = True,
        create_returncode: int = 0,
    ) -> None:
        self.existing_url = existing_url
        self.origin_available = origin_available
        self.gh_available = gh_available
        self.authenticated = authenticated
        self.create_returncode = create_returncode
        self.commands: list[list[str]] = []

    def __call__(
        self,
        command: list[str],
        _cwd: Path,
        _timeout_seconds: int,
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(command)
        if command[:2] == ["gh", "--version"] and not self.gh_available:
            raise FileNotFoundError(2, "not found", "gh")
        if len(command) >= 3 and command[:2] == ["git", "-C"]:
            args = command[3:]
            if args == ["remote", "get-url", "origin"]:
                return _completed(command, 0, "git@example.invalid:owner/repo.git\n") if (
                    self.origin_available
                ) else _completed(command, 2, stderr="no such remote")
            if args == ["branch", "--show-current"]:
                return _completed(command, 0, "aqr/T42\n")
            if args == ["push", "-u", "origin", "aqr/T42"]:
                return _completed(command, 0)
        if command[:3] == ["gh", "auth", "status"]:
            return _completed(command, 0) if self.authenticated else _completed(
                command, 1, stderr="not logged in"
            )
        if command[:3] == ["gh", "pr", "list"]:
            payload = [] if self.existing_url is None else [{"url": self.existing_url}]
            return _completed(command, 0, json.dumps(payload))
        if command[:3] == ["gh", "pr", "create"]:
            if self.create_returncode:
                return _completed(command, self.create_returncode, stderr="create denied")
            return _completed(command, 0, "https://github.example/owner/repo/pull/7\n")
        if command == ["gh", "--version"]:
            return _completed(command, 0, "gh version 2")
        raise AssertionError(f"unexpected command: {command}")


def _completed(
    command: list[str],
    returncode: int,
    stdout: str = "",
    *,
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(command, returncode, stdout, stderr)


def _publish(runner: FakeCommandRunner):
    return DraftPrPublisher(runner=runner).publish(
        "C:/worktrees/T42",
        branch="aqr/T42",
        title="Implement feature",
        task_id=42,
        run_id=9,
        agent="codex",
        commit_hash="abc123",
    )


def test_pushes_branch_and_creates_draft_pr_with_run_metadata() -> None:
    runner = FakeCommandRunner()

    result = _publish(runner)

    assert result.url == "https://github.example/owner/repo/pull/7"
    assert result.detail == "created draft PR"
    push = next(command for command in runner.commands if "push" in command)
    assert push[-4:] == ["push", "-u", "origin", "aqr/T42"]
    create = next(
        command
        for command in runner.commands
        if command[:3] == ["gh", "pr", "create"]
    )
    assert "--draft" in create
    assert create[create.index("--title") + 1] == "Implement feature"
    body = create[create.index("--body") + 1]
    assert "Task ID: 42" in body
    assert "Execution ID: 9" in body
    assert "Agent: codex" in body
    assert "Commit: abc123" in body


def test_existing_branch_pr_is_recorded_without_push_or_create() -> None:
    runner = FakeCommandRunner(existing_url="https://github.example/owner/repo/pull/3")

    result = _publish(runner)

    assert result.url == "https://github.example/owner/repo/pull/3"
    assert result.detail == "existing PR"
    assert not any("push" in command for command in runner.commands)
    assert not any(command[:3] == ["gh", "pr", "create"] for command in runner.commands)


@pytest.mark.parametrize(
    ("runner", "reason"),
    [
        (FakeCommandRunner(origin_available=False), "origin"),
        (FakeCommandRunner(gh_available=False), "unavailable"),
        (FakeCommandRunner(authenticated=False), "not authenticated"),
        (FakeCommandRunner(create_returncode=1), "failed: draft PR creation"),
    ],
)
def test_unavailable_pr_prerequisites_and_create_failure_are_recorded(
    runner: FakeCommandRunner,
    reason: str,
) -> None:
    result = _publish(runner)

    assert result.url is None
    assert reason in result.detail


def test_non_worktree_branch_is_rejected_before_any_command() -> None:
    runner = FakeCommandRunner()

    result = DraftPrPublisher(runner=runner).publish(
        "C:/project",
        branch=None,
        title="Unsafe branch",
        task_id=42,
        run_id=9,
        agent="codex",
        commit_hash="abc123",
    )

    assert result.url is None
    assert "requires the managed worktree branch" in result.detail
    assert runner.commands == []
