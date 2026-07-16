import pytest

from ai_queue_runner.domain.enums import TaskStatus
from ai_queue_runner.domain.transitions import (
    ALLOWED_TRANSITIONS,
    TERMINAL_STATUSES,
    InvalidTransitionError,
    can_transition,
    validate_transition,
)


def test_every_status_has_a_transition_entry():
    assert set(ALLOWED_TRANSITIONS) == set(TaskStatus)


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (TaskStatus.WAITING, TaskStatus.RESERVED),
        (TaskStatus.WAITING, TaskStatus.CANCELLED),
        (TaskStatus.RESERVED, TaskStatus.RUNNING),
        (TaskStatus.RESERVED, TaskStatus.WAITING),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.RUNNING, TaskStatus.RETRY_WAIT),
        (TaskStatus.RUNNING, TaskStatus.WAITING),  # usage limit mid-run (plan §7.2)
        (TaskStatus.RETRY_WAIT, TaskStatus.WAITING),
        (TaskStatus.FAILED, TaskStatus.WAITING),  # user re-run (plan §4.5)
    ],
)
def test_allowed_transitions(current, new):
    validate_transition(current, new)  # must not raise


@pytest.mark.parametrize(
    ("current", "new"),
    [
        (TaskStatus.WAITING, TaskStatus.RUNNING),  # must reserve first
        (TaskStatus.WAITING, TaskStatus.SUCCEEDED),
        (TaskStatus.SUCCEEDED, TaskStatus.WAITING),
        (TaskStatus.CANCELLED, TaskStatus.WAITING),
        (TaskStatus.RESERVED, TaskStatus.SUCCEEDED),
    ],
)
def test_illegal_transitions_raise(current, new):
    assert not can_transition(current, new)
    with pytest.raises(InvalidTransitionError):
        validate_transition(current, new)


def test_terminal_statuses():
    assert TERMINAL_STATUSES == {TaskStatus.SUCCEEDED, TaskStatus.CANCELLED}
