"""Periodic queue orchestration and background asynchronous execution."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import update
from sqlalchemy.orm import Session

from ai_queue_runner.application.agent_service import AgentService
from ai_queue_runner.application.execution_service import ExecutionService
from ai_queue_runner.application.recovery_service import (
    promote_retry_tasks,
    release_stale_reservations,
)
from ai_queue_runner.application.scheduler_service import (
    reserve_next_task,
    reserve_specific_task,
)
from ai_queue_runner.application.worktree_service import WorktreeService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import AgentName, AgentStatus, ApprovalMode, TaskStatus
from ai_queue_runner.domain.models import AgentState
from ai_queue_runner.infrastructure.adapters.base import AgentAdapter
from ai_queue_runner.infrastructure.process_runner import ProcessRunner
from ai_queue_runner.infrastructure.repositories import TaskRepository

SessionFactory = Callable[[], Session]
logger = logging.getLogger(__name__)


class QueueRuntime:
    """Coordinate periodic reservation and concurrent CLI execution off the UI thread."""

    _JOB_ID = "queue-runtime-tick"

    def __init__(
        self,
        session_factory: SessionFactory,
        config: AppConfig,
        adapters: Mapping[AgentName | str, AgentAdapter],
        process_runner: ProcessRunner | None = None,
        *,
        scheduler: Any | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._config = config
        self._adapters = {AgentName(name): adapter for name, adapter in adapters.items()}
        self._execution_service = ExecutionService(config, process_runner or ProcessRunner())
        self._worktree_service = WorktreeService(session_factory, config)
        self._scheduler = scheduler or BackgroundScheduler()

        self._tick_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._loop_lock = threading.Lock()
        self._active_lock = threading.Lock()
        self._started = False
        self._paused = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._active: dict[int, Future[object]] = {}

    @property
    def running(self) -> bool:
        with self._state_lock:
            return self._started and not self._paused

    @property
    def paused(self) -> bool:
        with self._state_lock:
            return self._started and self._paused

    def start(self) -> None:
        self._ensure_loop()
        run_initial_tick = False
        with self._state_lock:
            if not self._started:
                self._scheduler.add_job(
                    self.tick_once,
                    "interval",
                    seconds=self._config.scheduler.poll_interval_seconds,
                    id=self._JOB_ID,
                    coalesce=True,
                    max_instances=1,
                    replace_existing=True,
                )
                self._scheduler.start()
                self._started = True
                self._paused = False
                run_initial_tick = True
            elif self._paused:
                self._scheduler.resume()
                self._paused = False
                run_initial_tick = True
        if run_initial_tick:
            self.tick_once()

    def pause(self) -> None:
        with self._state_lock:
            if not self._started or self._paused:
                return
            self._scheduler.pause()
            self._paused = True

    def shutdown(self, *, wait: bool = True) -> None:
        with self._state_lock:
            if self._started:
                self._scheduler.shutdown(wait=wait)
                self._started = False
                self._paused = False
        self._stop_loop(wait=wait)

    def tick_once(self) -> tuple[int, ...]:
        """Run one maintenance/reservation pass and return newly reserved task IDs."""
        if not self._tick_lock.acquire(blocking=False):
            return ()
        try:
            now = utcnow()
            with self._session_factory() as session:
                promote_retry_tasks(session, now)
            AgentService(self._session_factory).promote_due_cooldowns(now)
            with self._session_factory() as session:
                release_stale_reservations(session, self._config, now)

            reserved_ids = []
            eligible_agents = frozenset(
                agent
                for agent in AgentName
                if (adapter := self._adapters.get(agent)) is not None
                and adapter.is_installed()
            )
            for agent in AgentName:
                if agent not in eligible_agents:
                    continue
                adapter = self._adapters[agent]
                excluded_task_ids: set[int] = set()
                reserved_task_id: int | None = None
                should_run = False
                while True:
                    with self._session_factory() as session:
                        task = reserve_next_task(
                            session,
                            self._config,
                            agent,
                            now,
                            eligible_agents=eligible_agents,
                            exclude_task_ids=excluded_task_ids,
                        )
                        if task is None:
                            break
                        if not self._worktree_service.prepare_reserved(session, task):
                            excluded_task_ids.add(task.id)
                            continue
                        reserved_task_id = task.id
                        should_run = (
                            self._config.app.global_auto_run
                            and task.approval_mode is ApprovalMode.AUTO
                        )
                    break
                if reserved_task_id is None:
                    continue
                reserved_ids.append(reserved_task_id)
                if should_run:
                    self._submit_execution(reserved_task_id, agent)
            return tuple(reserved_ids)
        finally:
            self._tick_lock.release()

    def approve(self, task_id: int) -> bool:
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            if task.status is not TaskStatus.RESERVED or task.assigned_agent is None:
                raise ValueError("only reserved tasks can be approved")
            if not self._worktree_service.prepare_reserved(session, task):
                raise ValueError("worktree unavailable; task returned to the waiting queue")
            agent = task.assigned_agent
        return self._submit_execution(task_id, agent)

    def reserve_now(self, task_id: int) -> bool:
        """Reserve exactly one user-selected waiting task for explicit approval."""
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            if task.status is not TaskStatus.WAITING:
                raise ValueError("only waiting tasks can run immediately")
            agents = (
                tuple(AgentName)
                if task.agent_policy.value == "any"
                else (AgentName(task.agent_policy.value),)
            )

        eligible_agents = frozenset(
            agent
            for agent in agents
            if (adapter := self._adapters.get(agent)) is not None
            and adapter.is_installed()
        )
        for agent in agents:
            if agent not in eligible_agents:
                continue
            with self._session_factory() as session:
                if (
                    (reserved := reserve_specific_task(
                        session,
                        self._config,
                        task_id,
                        agent,
                        eligible_agents=eligible_agents,
                    ))
                    is not None
                ):
                    if self._worktree_service.prepare_reserved(session, reserved):
                        return True
        raise ValueError("no compatible available agent can reserve this task")

    def reject(self, task_id: int, *, block: bool = False) -> bool:
        # AIDEV-NOTE: block=True parks the task in BLOCKED so the scheduler cannot
        # immediately re-reserve it and re-prompt the user; plain reject (block=False)
        # returns it to WAITING for callers that will cancel or re-queue it themselves.
        if self.is_active(task_id):
            raise ValueError("an active task cannot be rejected")
        with self._session_factory() as session:
            repo = TaskRepository(session)
            task = repo.get(task_id)
            if task is None:
                raise KeyError(f"task {task_id} not found")
            if task.status is not TaskStatus.RESERVED:
                raise ValueError("only reserved tasks can be rejected")
            assigned_agent = task.assigned_agent
            repo.set_status(task, TaskStatus.BLOCKED if block else TaskStatus.WAITING)
            if block:
                task.last_error = "사용자가 실행을 거부했습니다. 재시도 또는 취소를 선택하세요."
            task.assigned_agent = None
            task.reserved_at = None
            if assigned_agent is not None:
                session.execute(
                    update(AgentState)
                    .where(
                        AgentState.agent == assigned_agent,
                        AgentState.current_task_id == task_id,
                    )
                    .values(current_task_id=None, status=AgentStatus.AVAILABLE)
                )
            session.commit()
        return True

    def cancel_running(self, task_id: int) -> bool:
        with self._active_lock:
            future = self._active.get(task_id)
            if future is None or future.done():
                return False
        return future.cancel()

    def is_active(self, task_id: int | None = None) -> bool:
        if task_id is None:
            return self.running
        with self._active_lock:
            future = self._active.get(task_id)
            return future is not None and not future.done()

    def _submit_execution(self, task_id: int, agent: AgentName) -> bool:
        adapter = self._adapters.get(agent)
        if adapter is None:
            raise ValueError(f"no adapter configured for {agent}")
        loop = self._ensure_loop()
        with self._active_lock:
            existing = self._active.get(task_id)
            if existing is not None and not existing.done():
                return False
            future = asyncio.run_coroutine_threadsafe(
                self._execute_task(task_id, adapter),
                loop,
            )
            self._active[task_id] = future
        future.add_done_callback(lambda done: self._execution_done(task_id, done))
        return True

    async def _execute_task(self, task_id: int, adapter: AgentAdapter) -> None:
        # AIDEV-NOTE: an execution owns one fresh Session for its entire coroutine;
        # sharing the scheduler/UI Session across this loop thread is unsafe.
        with self._session_factory() as session:
            task = TaskRepository(session).get(task_id)
            if task is None or task.status is not TaskStatus.RESERVED:
                return
            if not self._worktree_service.prepare_reserved(session, task):
                return
            await self._execution_service.execute(session, task, adapter)

    def _execution_done(self, task_id: int, future: Future[object]) -> None:
        with self._active_lock:
            if self._active.get(task_id) is future:
                self._active.pop(task_id, None)
        if not future.cancelled() and (error := future.exception()) is not None:
            logger.error("task %s background execution failed: %s", task_id, error)

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=self._run_loop,
                args=(loop,),
                name="queue-runtime-asyncio",
                daemon=True,
            )
            self._loop = loop
            self._loop_thread = thread
            thread.start()
            return loop

    @staticmethod
    def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()
        loop.close()

    def _stop_loop(self, *, wait: bool) -> None:
        with self._loop_lock:
            loop = self._loop
            thread = self._loop_thread
            if loop is None or thread is None:
                return
            if loop.is_running():
                if wait:
                    drain = asyncio.run_coroutine_threadsafe(_cancel_pending_tasks(), loop)
                    try:
                        drain.result(timeout=10)
                    except (TimeoutError, RuntimeError) as exc:
                        logger.warning("async execution loop did not drain cleanly: %s", exc)
                else:
                    with self._active_lock:
                        active = list(self._active.values())
                    for future in active:
                        future.cancel()
                loop.call_soon_threadsafe(loop.stop)
            if wait:
                thread.join(timeout=10)
            self._loop = None
            self._loop_thread = None
        with self._active_lock:
            self._active.clear()


async def _cancel_pending_tasks() -> None:
    current = asyncio.current_task()
    tasks = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    for task in tasks:
        task.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
