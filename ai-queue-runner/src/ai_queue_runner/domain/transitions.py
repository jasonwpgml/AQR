"""Task status state machine (plan §4.3).

Any status change anywhere in the app must go through validate_transition so
illegal jumps (e.g. waiting -> running) fail loudly instead of corrupting the queue.
"""

from __future__ import annotations

from ai_queue_runner.domain.enums import TaskStatus

_S = TaskStatus

ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    _S.WAITING: frozenset({_S.RESERVED, _S.CANCELLED}),
    # reserved -> waiting: reservation released before the run started
    _S.RESERVED: frozenset({_S.RUNNING, _S.WAITING, _S.BLOCKED}),
    # running -> waiting: usage limit detected mid-run, task goes back to queue (plan §7.2)
    _S.RUNNING: frozenset({_S.SUCCEEDED, _S.FAILED, _S.RETRY_WAIT, _S.BLOCKED, _S.WAITING}),
    _S.RETRY_WAIT: frozenset({_S.WAITING, _S.CANCELLED}),
    _S.BLOCKED: frozenset({_S.WAITING, _S.CANCELLED}),
    # failed -> waiting: user re-runs a failed task (plan §4.5)
    _S.FAILED: frozenset({_S.WAITING}),
    _S.SUCCEEDED: frozenset(),
    _S.CANCELLED: frozenset(),
}

TERMINAL_STATUSES = frozenset(s for s, targets in ALLOWED_TRANSITIONS.items() if not targets)


class InvalidTransitionError(ValueError):
    def __init__(self, current: TaskStatus, new: TaskStatus) -> None:
        super().__init__(f"illegal task status transition: {current} -> {new}")
        self.current = current
        self.new = new


def can_transition(current: TaskStatus, new: TaskStatus) -> bool:
    return new in ALLOWED_TRANSITIONS[current]


def validate_transition(current: TaskStatus, new: TaskStatus) -> None:
    if not can_transition(current, new):
        raise InvalidTransitionError(current, new)
