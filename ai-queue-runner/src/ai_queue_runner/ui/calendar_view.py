"""Calendar projection of task creation, scheduling, and completion activity."""

from __future__ import annotations

from datetime import UTC, date, datetime

from PySide6.QtCore import QDate, Qt, Signal
from PySide6.QtGui import QColor, QFont, QTextCharFormat
from PySide6.QtWidgets import (
    QCalendarWidget,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.application.daily_summary_formatter import format_daily_summary
from ai_queue_runner.domain.enums import TaskStatus

_GROUPS = ("완료", "실패·취소", "예정", "생성")
_STATUS_LABELS = {
    TaskStatus.WAITING: "대기",
    TaskStatus.RESERVED: "예약",
    TaskStatus.RUNNING: "실행",
    TaskStatus.SUCCEEDED: "완료",
    TaskStatus.FAILED: "실패",
    TaskStatus.RETRY_WAIT: "재시도 대기",
    TaskStatus.BLOCKED: "차단",
    TaskStatus.CANCELLED: "취소",
}


class CalendarView(QWidget):
    """Display task activity using local dates while querying with UTC boundaries."""

    focus_task_requested = Signal(int)
    add_task_requested = Signal(object)

    def __init__(self, task_service: object, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._task_service = task_service
        self._activities: list[object] = []
        self.marked_dates: set[date] = set()

        self.calendar = QCalendarWidget()
        self.calendar.setAccessibleName("작업 달력")
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(
            QCalendarWidget.VerticalHeaderFormat.NoVerticalHeader
        )
        self.calendar.selectionChanged.connect(self._show_selected_date)
        self.calendar.currentPageChanged.connect(lambda _year, _month: self.refresh())

        self.day_label = QLabel()
        self.task_tree = QTreeWidget()
        self.task_tree.setAccessibleName("선택 날짜 작업")
        self.task_tree.setHeaderHidden(True)
        self.task_tree.itemDoubleClicked.connect(self._task_double_clicked)

        self.add_button = QPushButton("이 날짜에 작업 추가")
        self.add_button.clicked.connect(self._request_add_task)
        self.summary_button = QPushButton("이 날짜 요약")
        self.summary_button.clicked.connect(self._show_daily_summary)

        details_header = QHBoxLayout()
        details_header.addWidget(self.day_label)
        details_header.addStretch(1)
        details_header.addWidget(self.summary_button)
        details_header.addWidget(self.add_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self.calendar, 2)
        layout.addLayout(details_header)
        layout.addWidget(self.task_tree, 1)

        self.refresh()

    def refresh(self) -> None:
        start_utc, end_utc = _visible_month_utc_range(
            self.calendar.yearShown(),
            self.calendar.monthShown(),
        )
        try:
            self._activities = list(
                self._task_service.activity_between(start_utc, end_utc)
            )
        except Exception as exc:
            self._activities = []
            self.day_label.setText(f"달력 새로고침 실패: {exc}")
        self._mark_activity_dates()
        self._show_selected_date()

    def _mark_activity_dates(self) -> None:
        for marked_date in self.marked_dates:
            self.calendar.setDateTextFormat(_to_qdate(marked_date), QTextCharFormat())
        self.marked_dates = {
            local_date
            for activity in self._activities
            for value in (
                getattr(activity, "created_at", None),
                getattr(activity, "finished_at", None),
                getattr(activity, "not_before", None),
            )
            if value is not None
            if (local_date := _utc_naive_to_local(value).date())
        }
        activity_format = QTextCharFormat()
        activity_format.setFontWeight(QFont.Weight.Bold)
        activity_format.setForeground(QColor("#5b21b6"))
        activity_format.setBackground(QColor("#ede9fe"))
        for marked_date in self.marked_dates:
            self.calendar.setDateTextFormat(_to_qdate(marked_date), activity_format)

    def _show_selected_date(self) -> None:
        selected = self.calendar.selectedDate()
        selected_date = date(selected.year(), selected.month(), selected.day())
        self.day_label.setText(selected_date.strftime("%Y년 %m월 %d일"))
        grouped: dict[str, list[object]] = {name: [] for name in _GROUPS}
        for activity in self._activities:
            status = getattr(activity, "status", None)
            if _is_local_date(getattr(activity, "finished_at", None), selected_date):
                if status is TaskStatus.SUCCEEDED:
                    grouped["완료"].append(activity)
                elif status in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
                    grouped["실패·취소"].append(activity)
            if _is_local_date(getattr(activity, "not_before", None), selected_date):
                grouped["예정"].append(activity)
            if _is_local_date(getattr(activity, "created_at", None), selected_date):
                grouped["생성"].append(activity)

        self.task_tree.clear()
        for group_name in _GROUPS:
            activities = grouped[group_name]
            group_item = QTreeWidgetItem([f"{group_name} ({len(activities)})"])
            group_item.setFlags(group_item.flags() & ~Qt.ItemFlag.ItemIsSelectable)
            self.task_tree.addTopLevelItem(group_item)
            for activity in activities:
                status = getattr(activity, "status", None)
                status_label = _STATUS_LABELS.get(status, str(getattr(status, "value", status)))
                task_item = QTreeWidgetItem(
                    [f"[{status_label}] {getattr(activity, 'title', '')}"]
                )
                task_item.setData(0, Qt.ItemDataRole.UserRole, int(activity.id))
                group_item.addChild(task_item)
            group_item.setExpanded(True)

    def _task_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        task_id = item.data(0, Qt.ItemDataRole.UserRole)
        if task_id is not None:
            self.focus_task_requested.emit(int(task_id))

    def _request_add_task(self) -> None:
        selected = self.calendar.selectedDate()
        local_nine_am = datetime(
            selected.year(),
            selected.month(),
            selected.day(),
            9,
        ).astimezone()
        self.add_task_requested.emit(local_nine_am)

    def _show_daily_summary(self) -> None:
        selected = self.calendar.selectedDate()
        selected_date = date(selected.year(), selected.month(), selected.day())
        try:
            summary = self._task_service.daily_summary(selected_date)
        except Exception as exc:
            QMessageBox.critical(self, "일일 요약 실패", str(exc))
            return
        QMessageBox.information(
            self,
            f"{selected_date.isoformat()} 일일 요약",
            format_daily_summary(summary),
        )


def _visible_month_utc_range(year: int, month: int) -> tuple[datetime, datetime]:
    start_local = datetime(year, month, 1).astimezone()
    if month == 12:
        end_local = datetime(year + 1, 1, 1).astimezone()
    else:
        end_local = datetime(year, month + 1, 1).astimezone()
    return (
        start_local.astimezone(UTC).replace(tzinfo=None),
        end_local.astimezone(UTC).replace(tzinfo=None),
    )


def _utc_naive_to_local(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC).astimezone()


def _is_local_date(value: datetime | None, expected: date) -> bool:
    return value is not None and _utc_naive_to_local(value).date() == expected


def _to_qdate(value: date) -> QDate:
    return QDate(value.year, value.month, value.day)
