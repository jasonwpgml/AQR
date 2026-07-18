"""Publish an automatically committed managed worktree as a GitHub draft PR."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

CommandRunner = Callable[
    [list[str], Path, int],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True)
class DraftPrResult:
    url: str | None
    detail: str


class DraftPrPublisher:
    def __init__(
        self,
        *,
        timeout_seconds: int = 30,
        runner: CommandRunner | None = None,
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._runner = runner or _run_command

    def publish(
        self,
        cwd: Path | str,
        *,
        branch: str | None,
        title: str,
        task_id: int,
        run_id: int,
        agent: str,
        commit_hash: str,
    ) -> DraftPrResult:
        expected_branch = f"aqr/T{task_id}"
        if branch != expected_branch:
            return DraftPrResult(
                None,
                "skipped: draft PR publishing requires the managed worktree branch "
                f"{expected_branch}",
            )

        path = Path(cwd).resolve()
        try:
            origin = self._git(path, "remote", "get-url", "origin")
            if origin.returncode != 0 or not origin.stdout.strip():
                return DraftPrResult(None, "skipped: Git remote 'origin' is unavailable")

            current_branch = self._git(path, "branch", "--show-current")
            if current_branch.returncode != 0:
                return self._failed("branch check", current_branch)
            if current_branch.stdout.strip() != branch:
                return DraftPrResult(
                    None,
                    f"skipped: execution checkout is not on expected branch {branch}",
                )

            gh_version = self._run(["gh", "--version"], path)
            if gh_version.returncode != 0:
                return DraftPrResult(None, "skipped: GitHub CLI (gh) is unavailable")
            auth = self._run(["gh", "auth", "status"], path)
            if auth.returncode != 0:
                return DraftPrResult(None, "skipped: GitHub CLI is not authenticated")

            existing = self._run(
                [
                    "gh",
                    "pr",
                    "list",
                    "--head",
                    branch,
                    "--state",
                    "all",
                    "--json",
                    "url",
                    "--limit",
                    "1",
                ],
                path,
            )
            if existing.returncode != 0:
                return self._failed("existing PR lookup", existing)
            existing_url = _first_pr_url(existing.stdout)
            if existing_url:
                return DraftPrResult(existing_url, "existing PR")

            pushed = self._git(path, "push", "-u", "origin", branch)
            if pushed.returncode != 0:
                return self._failed("branch push", pushed)

            created = self._run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--draft",
                    "--title",
                    _normalize_title(title),
                    "--body",
                    _pr_body(task_id, run_id, agent, commit_hash),
                    "--head",
                    branch,
                ],
                path,
            )
            if created.returncode != 0:
                return self._failed("draft PR creation", created)
            url = _output_url(created.stdout)
            if url is None:
                return DraftPrResult(None, "failed: draft PR creation returned no URL")
            return DraftPrResult(url, "created draft PR")
        except FileNotFoundError as exc:
            if exc.filename == "gh":
                return DraftPrResult(None, "skipped: GitHub CLI (gh) is unavailable")
            return self._exception_failure(exc)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._exception_failure(exc)

    def _git(self, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
        return self._run(["git", "-C", str(cwd), *args], cwd)

    def _run(self, command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        return self._runner(command, cwd, self._timeout_seconds)

    @staticmethod
    def _failed(
        operation: str,
        completed: subprocess.CompletedProcess[str],
    ) -> DraftPrResult:
        detail = completed.stderr.strip() or completed.stdout.strip()
        if not detail:
            detail = f"command exited with code {completed.returncode}"
        return DraftPrResult(None, f"failed: {operation}: {_compact(detail)}")

    @staticmethod
    def _exception_failure(exc: BaseException) -> DraftPrResult:
        return DraftPrResult(
            None,
            f"failed: {type(exc).__name__}: {_compact(str(exc))}",
        )


def _run_command(
    command: list[str],
    cwd: Path,
    timeout_seconds: int,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        shell=False,
        timeout=timeout_seconds,
    )


def _first_pr_url(output: str) -> str | None:
    try:
        payload = json.loads(output or "[]")
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], dict):
        return None
    url = payload[0].get("url")
    return url.strip() if isinstance(url, str) and url.strip() else None


def _output_url(output: str) -> str | None:
    for line in reversed(output.splitlines()):
        candidate = line.strip()
        if candidate.startswith(("https://", "http://")):
            return candidate
    return None


def _normalize_title(title: str) -> str:
    return " ".join(title.replace("\x00", " ").split()) or "untitled task"


def _pr_body(task_id: int, run_id: int, agent: str, commit_hash: str) -> str:
    return (
        f"Task ID: {task_id}\n"
        f"Execution ID: {run_id}\n"
        f"Agent: {agent}\n"
        f"Commit: {commit_hash}"
    )


def _compact(value: str) -> str:
    return " ".join(value.split())[:500] or "unknown error"
