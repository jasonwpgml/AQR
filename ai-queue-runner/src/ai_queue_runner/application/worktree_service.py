"""Application policy around worktree preparation and explicit cleanup."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import update
from sqlalchemy.orm import Session

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import AgentStatus, TaskStatus
from ai_queue_runner.domain.models import AgentState, Task
from ai_queue_runner.infrastructure.git_worktrees import (
    GitWorktreeError,
    GitWorktreeManager,
)
from ai_queue_runner.infrastructure.repositories import TaskRepository, TaskRunRepository

SessionFactory = Callable[[], Session]
logger = logging.getLogger(__name__)

_CLEANUP_STATUSES = frozenset(
    {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
)


@dataclass(frozen=True)
class WorktreeCleanupView:
    task_id: int
    path: str
    branch: str


class WorktreeService:
    def __init__(self, session_factory: SessionFactory, config: AppConfig) -> None:
        self._session_factory = session_factory
        self._manager = GitWorktreeManager(
            config.app.worktrees_dir,
            timeout_seconds=config.scheduler.worktree_git_timeout_seconds,
        )

    def prepare_reserved(self, session: Session, task: Task) -> bool:
        """Prepare an isolated checkout or release the reservation for serial fallback."""
        if task.status is not TaskStatus.RESERVED:
            raise ValueError("only a reserved task can prepare a worktree")
        if not task.use_worktree:
            return True

        had_prepared_worktree = bool(task.worktree_path and task.worktree_branch)
        try:
            prepared = self._manager.prepare(task.project_path, task.id)
        except GitWorktreeError as exc:
            TaskRepository(session).set_status(task, TaskStatus.WAITING)
            assigned_agent = task.assigned_agent
            task.assigned_agent = None
            task.reserved_at = None
            if not had_prepared_worktree:
                task.use_worktree = False
                task.worktree_path = None
                task.worktree_branch = None
            task.last_error = f"worktree unavailable; using exclusive fallback: {exc}"
            if assigned_agent is not None:
                session.execute(
                    update(AgentState)
                    .where(
                        AgentState.agent == assigned_agent,
                        AgentState.current_task_id == task.id,
                    )
                    .values(current_task_id=None, status=AgentStatus.AVAILABLE)
                )
            session.commit()
            logger.warning("task %s worktree preparation failed: %s", task.id, exc)
            return False

        task.worktree_path = str(prepared.path)
        task.worktree_branch = prepared.branch
        session.commit()
        logger.info(
            "task %s worktree prepared: branch=%s path=%s",
            task.id,
            prepared.branch,
            prepared.path,
        )
        return True

    def cleanup(self, task_id: int) -> WorktreeCleanupView:
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            if task.status not in _CLEANUP_STATUSES:
                raise ValueError("worktrees can only be cleaned up for completed tasks")
            runs = TaskRunRepository(session).list_for_task(task_id)
            latest_worktree_run = next(
                (run for run in reversed(runs) if run.worktree_path and run.worktree_branch),
                None,
            )
            path = task.worktree_path or (
                None if latest_worktree_run is None else latest_worktree_run.worktree_path
            )
            branch = task.worktree_branch or (
                None if latest_worktree_run is None else latest_worktree_run.worktree_branch
            )
            if not path or not branch:
                raise ValueError(f"task {task_id} has no recorded worktree")
            project_path = task.project_path

        removed = self._manager.cleanup(project_path, task_id, Path(path), branch)
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            if task is not None:
                task.use_worktree = False
                session.commit()
        return WorktreeCleanupView(
            task_id=task_id,
            path=str(removed.path),
            branch=removed.branch,
        )
