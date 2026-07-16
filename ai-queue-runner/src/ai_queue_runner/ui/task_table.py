"""Compact Qt model for the task queue table."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QApplication

from ai_queue_runner.domain.enums import AgentPolicy, TaskStatus


class TaskColumn(IntEnum):
    PRIORITY = 0
    AGENT = 1
    STATUS = 2
    TITLE = 3
    PROJECT = 4


_HEADERS = ("우선순위", "AI", "상태", "제목", "프로젝트")
_STATUS_LABELS = {
    TaskStatus.WAITING.value: "대기",
    TaskStatus.RESERVED.value: "예약",
    TaskStatus.RUNNING.value: "실행 중",
    TaskStatus.RETRY_WAIT.value: "재시도 대기",
    TaskStatus.BLOCKED.value: "차단됨",
    TaskStatus.FAILED.value: "실패",
    TaskStatus.SUCCEEDED.value: "완료",
    TaskStatus.CANCELLED.value: "취소됨",
}
_AGENT_LABELS = {
    AgentPolicy.CLAUDE.value: "Claude",
    AgentPolicy.CODEX.value: "Codex",
    AgentPolicy.ANY.value: "Any",
}
_STATUS_COLORS = {
    TaskStatus.RUNNING.value: QColor("#2563eb"),
    TaskStatus.RETRY_WAIT.value: QColor("#b45309"),
    TaskStatus.BLOCKED.value: QColor("#b45309"),
    TaskStatus.FAILED.value: QColor("#b91c1c"),
    TaskStatus.SUCCEEDED.value: QColor("#15803d"),
    TaskStatus.CANCELLED.value: QColor("#6b7280"),
}


def enum_value(value: object) -> str:
    """Return a stable string for either a StrEnum or a DTO string."""
    raw = getattr(value, "value", value)
    return "" if raw is None else str(raw)


class TaskTableModel(QAbstractTableModel):
    """Read-only queue projection; persistence stays behind ``TaskService``."""

    def __init__(self, tasks: list[object] | None = None, parent: object | None = None) -> None:
        super().__init__(parent)
        self._tasks = _queue_order(tasks or ())

    def rowCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(self._tasks)

    def columnCount(self, parent: QModelIndex | None = None) -> int:  # noqa: N802
        return 0 if parent is not None and parent.isValid() else len(_HEADERS)

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(_HEADERS):
                return _HEADERS[section]
        return super().headerData(section, orientation, role)

    def data(
        self,
        index: QModelIndex,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._tasks):
            return None
        task = self._tasks[index.row()]
        column = TaskColumn(index.column())

        if role == Qt.ItemDataRole.UserRole:
            return task
        if role == Qt.ItemDataRole.ToolTipRole:
            last_error = getattr(task, "last_error", None)
            return f"마지막 오류:\n{last_error}" if last_error else None
        if role == Qt.ItemDataRole.TextAlignmentRole and column is TaskColumn.PRIORITY:
            return Qt.AlignmentFlag.AlignCenter
        if role == Qt.ItemDataRole.ForegroundRole and column is TaskColumn.STATUS:
            color = _STATUS_COLORS.get(enum_value(getattr(task, "status", "")))
            return color or QApplication.palette().text()
        if role != Qt.ItemDataRole.DisplayRole:
            return None

        if column is TaskColumn.PRIORITY:
            return getattr(task, "priority", 0)
        if column is TaskColumn.AGENT:
            assigned = enum_value(getattr(task, "assigned_agent", None))
            policy = enum_value(getattr(task, "agent_policy", ""))
            return _AGENT_LABELS.get(assigned or policy, assigned or policy)
        if column is TaskColumn.STATUS:
            if bool(getattr(task, "archived", False)):
                return "보관됨"
            status = enum_value(getattr(task, "status", ""))
            if status == TaskStatus.RETRY_WAIT.value:
                return _retry_wait_label(getattr(task, "not_before", None))
            return _STATUS_LABELS.get(status, status)
        if column is TaskColumn.TITLE:
            return getattr(task, "title", "")
        project_path = str(getattr(task, "project_path", ""))
        return Path(project_path).name or project_path

    def set_tasks(self, tasks: list[object]) -> None:
        self.beginResetModel()
        self._tasks = _queue_order(tasks)
        self.endResetModel()

    @property
    def tasks(self) -> tuple[object, ...]:
        return tuple(self._tasks)

    def task_at(self, row: int) -> object | None:
        if 0 <= row < len(self._tasks):
            return self._tasks[row]
        return None


def _queue_order(tasks: Iterable[object]) -> list[object]:
    return sorted(
        list(tasks),
        key=lambda task: (
            -int(getattr(task, "priority", 0)),
            int(getattr(task, "id", 0)),
        ),
    )


def _retry_wait_label(not_before: object) -> str:
    if not isinstance(not_before, datetime):
        return _STATUS_LABELS[TaskStatus.RETRY_WAIT.value]
    utc_value = not_before.replace(tzinfo=UTC) if not_before.tzinfo is None else not_before
    return f"재시도 대기 ({utc_value.astimezone():%H:%M})"
