"""Agent availability controls."""

from __future__ import annotations

from datetime import UTC

from PySide6.QtCore import QDateTime, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QFormLayout,
    QGroupBox,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.domain.enums import AgentName, AgentStatus
from ai_queue_runner.ui.task_table import enum_value

_STATUS_LABELS = {
    AgentStatus.AVAILABLE: "사용 가능",
    AgentStatus.BUSY: "실행 중",
    AgentStatus.COOLDOWN: "쿨다운",
    AgentStatus.DISABLED: "사용 안 함",
    AgentStatus.ERROR: "오류",
    AgentStatus.UNKNOWN: "확인 안 됨",
}


class AgentStatusWidget(QGroupBox):
    """One agent card that emits service-ready availability values."""

    availability_changed = Signal(object, object, object)

    def __init__(self, agent: AgentName, parent: QWidget | None = None) -> None:
        super().__init__(agent.value.title(), parent)
        self.agent = agent
        self.status_label = QLabel("확인 안 됨")
        self.current_task_label = QLabel("현재 작업: 없음")
        self.cli_warning_label = QLabel()
        self.cli_warning_label.setWordWrap(True)
        self.cli_warning_label.setStyleSheet("color: #b91c1c;")
        self.cli_warning_label.hide()
        self.current_task_label.setTextInteractionFlags(
            self.current_task_label.textInteractionFlags()
        )

        self.status_combo = QComboBox()
        for status in (
            AgentStatus.AVAILABLE,
            AgentStatus.COOLDOWN,
            AgentStatus.DISABLED,
        ):
            self.status_combo.addItem(_STATUS_LABELS[status], status)

        self.available_at_check = QCheckBox("사용 가능 시각")
        self.available_at_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.available_at_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.available_at_edit.setCalendarPopup(True)
        self.available_at_edit.setEnabled(False)
        self.available_at_check.toggled.connect(self.available_at_edit.setEnabled)

        self.apply_button = QPushButton("상태 적용")
        self.apply_button.clicked.connect(self._emit_availability)

        form = QFormLayout()
        form.addRow("현재 상태", self.status_label)
        form.addRow("변경", self.status_combo)
        form.addRow(self.available_at_check, self.available_at_edit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.addLayout(form)
        layout.addWidget(self.current_task_label)
        layout.addWidget(self.cli_warning_label)
        layout.addWidget(self.apply_button)

    def set_state(self, state: object | None) -> None:
        if state is None:
            self.status_label.setText(_STATUS_LABELS[AgentStatus.UNKNOWN])
            self.current_task_label.setText("현재 작업: 없음")
            return
        status = AgentStatus(enum_value(getattr(state, "status", AgentStatus.UNKNOWN)))
        self.status_label.setText(_STATUS_LABELS[status])
        current_task_id = getattr(state, "current_task_id", None)
        self.current_task_label.setText(
            f"현재 작업: #{current_task_id}" if current_task_id is not None else "현재 작업: 없음"
        )
        self._select_status(status)
        available_at = getattr(state, "available_at", None)
        self.available_at_check.setChecked(available_at is not None)
        if available_at is not None:
            local = available_at.replace(tzinfo=UTC).astimezone()
            self.available_at_edit.setDateTime(QDateTime(local))
        error = getattr(state, "last_error", None)
        self.setToolTip(str(error) if error else "")

    def set_cli_resolvable(self, *, enabled: bool, resolvable: bool) -> None:
        missing = enabled and not resolvable
        if missing:
            self.cli_warning_label.setText(
                f"{self.agent.value} CLI를 찾을 수 없습니다. "
                "설정에서 executable 경로를 지정하세요."
            )
            self.cli_warning_label.show()
        else:
            self.cli_warning_label.clear()
            self.cli_warning_label.hide()
        self.status_combo.setEnabled(not missing)
        self.available_at_check.setEnabled(not missing)
        self.available_at_edit.setEnabled(not missing and self.available_at_check.isChecked())
        self.apply_button.setEnabled(not missing)

    def _select_status(self, status: AgentStatus) -> None:
        for index in range(self.status_combo.count()):
            if self.status_combo.itemData(index) is status:
                self.status_combo.setCurrentIndex(index)
                return

    def _emit_availability(self) -> None:
        available_at = None
        if self.available_at_check.isChecked():
            local = self.available_at_edit.dateTime().toPython()
            if local.tzinfo is None:
                local = local.astimezone()
            available_at = local.astimezone(UTC).replace(tzinfo=None)
        self.availability_changed.emit(self.agent, self.status_combo.currentData(), available_at)
