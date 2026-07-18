"""Conservative Git preflight and commit operations for successful task runs."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

COMMIT_SUBJECT_TEMPLATE = "aqr: {title} (task #{task_id})"
COMMIT_BODY_TEMPLATE = "Execution ID: {run_id}\nAgent: {agent}"


@dataclass(frozen=True)
class AutoCommitResult:
    commit_hash: str | None
    detail: str


@dataclass(frozen=True)
class AutoCommitPreflight:
    blocked_result: AutoCommitResult | None = None


class GitAutoCommitter:
    def __init__(self, *, timeout_seconds: int = 30) -> None:
        self._timeout_seconds = timeout_seconds

    def preflight(self, cwd: Path | str) -> AutoCommitPreflight:
        path = Path(cwd).resolve()
        try:
            repository_check = self._run(
                path,
                ["rev-parse", "--is-inside-work-tree"],
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return AutoCommitPreflight(self._failed(exc))
        if (
            repository_check.returncode != 0
            or repository_check.stdout.strip() != "true"
        ):
            return AutoCommitPreflight(
                AutoCommitResult(None, "skipped: not a Git repository")
            )

        try:
            status = self._run(path, ["status", "--porcelain"])
        except (OSError, subprocess.TimeoutExpired) as exc:
            return AutoCommitPreflight(self._failed(exc))
        if status.returncode != 0:
            return AutoCommitPreflight(self._failed(status))
        if status.stdout.strip():
            return AutoCommitPreflight(
                AutoCommitResult(
                    None,
                    "skipped: repository had changes before execution",
                )
            )
        return AutoCommitPreflight()

    def commit(
        self,
        cwd: Path | str,
        preflight: AutoCommitPreflight,
        *,
        title: str,
        task_id: int,
        run_id: int,
        agent: str,
    ) -> AutoCommitResult:
        if preflight.blocked_result is not None:
            return preflight.blocked_result

        path = Path(cwd).resolve()
        try:
            status = self._run(path, ["status", "--porcelain"])
            if status.returncode != 0:
                return self._failed(status)
            if not status.stdout.strip():
                return AutoCommitResult(None, "skipped: no changes after execution")

            staged = self._run(path, ["add", "-A"])
            if staged.returncode != 0:
                return self._failed(staged)

            subject, body = format_commit_message(title, task_id, run_id, agent)
            committed = self._run(path, ["commit", "-m", subject, "-m", body])
            if committed.returncode != 0:
                return self._failed(committed)

            resolved = self._run(path, ["rev-parse", "HEAD"])
            if resolved.returncode != 0 or not resolved.stdout.strip():
                return self._failed(resolved)
            return AutoCommitResult(resolved.stdout.strip(), "committed")
        except (OSError, subprocess.TimeoutExpired) as exc:
            return self._failed(exc)

    def _run(
        self,
        cwd: Path,
        args: list[str],
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="replace",
            shell=False,
            timeout=self._timeout_seconds,
        )

    @staticmethod
    def _failed(
        failure: subprocess.CompletedProcess[str] | BaseException,
    ) -> AutoCommitResult:
        if isinstance(failure, subprocess.CompletedProcess):
            detail = failure.stderr.strip() or failure.stdout.strip()
            if not detail:
                detail = f"Git exited with code {failure.returncode}"
        else:
            detail = f"{type(failure).__name__}: {failure}"
        compact = " ".join(detail.split())[:500]
        return AutoCommitResult(None, f"failed: {compact or 'unknown Git error'}")


def format_commit_message(
    title: str,
    task_id: int,
    run_id: int,
    agent: str,
) -> tuple[str, str]:
    normalized_title = " ".join(title.replace("\x00", " ").split()) or "untitled task"
    return (
        COMMIT_SUBJECT_TEMPLATE.format(title=normalized_title, task_id=task_id),
        COMMIT_BODY_TEMPLATE.format(run_id=run_id, agent=agent),
    )
