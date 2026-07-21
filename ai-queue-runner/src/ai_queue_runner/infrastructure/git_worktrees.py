"""Deterministic Git worktree creation, reuse, and conservative cleanup."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitWorktreeError(ValueError):
    """Raised when a worktree operation cannot be completed safely."""


@dataclass(frozen=True)
class PreparedWorktree:
    path: Path
    branch: str


@dataclass(frozen=True)
class RemovedWorktree:
    path: Path
    branch: str


@dataclass(frozen=True)
class _WorktreeEntry:
    path: Path
    branch_ref: str | None


class GitWorktreeManager:
    """Own Git subprocess calls without ever merging or deleting branches."""

    def __init__(self, worktrees_dir: Path, *, timeout_seconds: int = 30) -> None:
        self._worktrees_dir = worktrees_dir.resolve()
        self._timeout_seconds = timeout_seconds

    def prepare(self, project_path: Path | str, task_id: int) -> PreparedWorktree:
        project = Path(project_path).resolve()
        repository = self._repository_root(project)
        try:
            relative_project = project.relative_to(repository)
        except ValueError as exc:
            raise GitWorktreeError(f"project is outside its Git repository: {project}") from exc

        branch = self.branch_name(task_id)
        branch_ref = f"refs/heads/{branch}"
        target = (self._worktrees_dir / f"T{task_id}").resolve()
        self._require_managed_path(target)

        entries = self._list_worktrees(repository)
        matching = next((entry for entry in entries if entry.branch_ref == branch_ref), None)
        if matching is not None and matching.path.is_dir():
            if not _same_path(matching.path, target):
                raise GitWorktreeError(
                    f"branch {branch} is checked out outside its managed path: {matching.path}"
                )
            self._require_managed_path(matching.path)
            return self._prepared_path(matching.path, relative_project, branch)
        if matching is not None:
            self._run(repository, ["worktree", "prune"])
            entries = self._list_worktrees(repository)

        occupied = next((entry for entry in entries if _same_path(entry.path, target)), None)
        if occupied is not None:
            raise GitWorktreeError(
                f"managed worktree path is registered to another branch: {target}"
            )
        if target.exists():
            raise GitWorktreeError(f"managed worktree path already exists: {target}")

        target.parent.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(repository, branch_ref):
            raise GitWorktreeError(
                f"branch {branch} already exists without its managed worktree"
            )
        self._run(repository, ["worktree", "add", "-b", branch, str(target), "HEAD"])
        return self._prepared_path(target, relative_project, branch)

    def cleanup(
        self,
        project_path: Path | str,
        task_id: int,
        recorded_path: Path | str,
        recorded_branch: str,
    ) -> RemovedWorktree:
        project = Path(project_path).resolve()
        repository = self._repository_root(project)
        branch = self.branch_name(task_id)
        if recorded_branch != branch:
            raise GitWorktreeError(
                f"task #{task_id} worktree branch mismatch: {recorded_branch}"
            )

        execution_path = Path(recorded_path).resolve()
        if not execution_path.is_dir():
            raise GitWorktreeError(f"worktree path does not exist: {execution_path}")
        worktree_root = self._repository_root(execution_path)
        self._require_managed_path(worktree_root)

        branch_ref = f"refs/heads/{branch}"
        registered = next(
            (
                entry
                for entry in self._list_worktrees(repository)
                if entry.branch_ref == branch_ref and _same_path(entry.path, worktree_root)
            ),
            None,
        )
        if registered is None:
            raise GitWorktreeError(
                f"worktree is not registered for {branch}: {worktree_root}"
            )

        if self._run(worktree_root, ["status", "--porcelain"]).stdout.strip():
            raise GitWorktreeError(
                f"worktree has uncommitted changes; refusing cleanup: {worktree_root}"
            )
        if not self._branch_exists(repository, branch_ref):
            raise GitWorktreeError(f"worktree branch no longer exists: {branch}")
        merged = self._run(
            repository,
            ["merge-base", "--is-ancestor", branch_ref, "HEAD"],
            check=False,
        )
        if merged.returncode == 1:
            raise GitWorktreeError(
                f"branch {branch} has commits not merged into the project HEAD"
            )
        if merged.returncode != 0:
            raise GitWorktreeError(self._failure_message(merged))

        self._run(repository, ["worktree", "remove", str(worktree_root)])
        return RemovedWorktree(path=worktree_root, branch=branch)

    @staticmethod
    def branch_name(task_id: int) -> str:
        if task_id <= 0:
            raise GitWorktreeError("task ID must be positive")
        return f"aqr/T{task_id}"

    def _repository_root(self, path: Path) -> Path:
        completed = self._run(path, ["rev-parse", "--show-toplevel"], check=False)
        if completed.returncode != 0:
            raise GitWorktreeError(f"not a Git repository: {path}")
        root = completed.stdout.strip()
        if not root:
            raise GitWorktreeError(f"Git did not report a repository root: {path}")
        return Path(root).resolve()

    def _prepared_path(
        self,
        worktree_root: Path,
        relative_project: Path,
        branch: str,
    ) -> PreparedWorktree:
        execution_path = (worktree_root / relative_project).resolve()
        if not execution_path.is_dir():
            raise GitWorktreeError(
                f"project subdirectory is missing from worktree: {execution_path}"
            )
        return PreparedWorktree(path=execution_path, branch=branch)

    def _list_worktrees(self, repository: Path) -> list[_WorktreeEntry]:
        output = self._run(repository, ["worktree", "list", "--porcelain"]).stdout
        entries: list[_WorktreeEntry] = []
        path: Path | None = None
        branch_ref: str | None = None
        for line in [*output.splitlines(), ""]:
            if line.startswith("worktree "):
                path = Path(line.removeprefix("worktree ")).resolve()
            elif line.startswith("branch "):
                branch_ref = line.removeprefix("branch ")
            elif not line and path is not None:
                entries.append(_WorktreeEntry(path=path, branch_ref=branch_ref))
                path = None
                branch_ref = None
        return entries

    def _branch_exists(self, repository: Path, branch_ref: str) -> bool:
        completed = self._run(
            repository,
            ["show-ref", "--verify", "--quiet", branch_ref],
            check=False,
        )
        if completed.returncode not in {0, 1}:
            raise GitWorktreeError(self._failure_message(completed))
        return completed.returncode == 0

    def _require_managed_path(self, path: Path) -> None:
        if path == self._worktrees_dir or not path.is_relative_to(self._worktrees_dir):
            raise GitWorktreeError(f"worktree path is outside the configured root: {path}")

    def _run(
        self,
        cwd: Path,
        args: list[str],
        *,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["git", "-C", str(cwd), *args],
                capture_output=True,
                check=False,
                encoding="utf-8",
                errors="replace",
                shell=False,
                timeout=self._timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GitWorktreeError(f"Git command failed to start: {exc}") from exc
        if check and completed.returncode != 0:
            raise GitWorktreeError(self._failure_message(completed))
        return completed

    @staticmethod
    def _failure_message(completed: subprocess.CompletedProcess[str]) -> str:
        detail = completed.stderr.strip() or completed.stdout.strip() or "unknown Git error"
        return f"Git command failed ({completed.returncode}): {detail}"


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(str(right.resolve()))
