"""Task create/edit dialog."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from pydantic import ValidationError
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.application.task_service import TaskDraft
from ai_queue_runner.domain.enums import AgentPolicy, ApprovalMode
from ai_queue_runner.ui.task_table import enum_value


def _utc_naive_to_local(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC).astimezone()


def _local_to_utc_naive(value: datetime) -> datetime:
    local = value if value.tzinfo is not None else value.astimezone()
    return local.astimezone(UTC).replace(tzinfo=None)


class TaskDialog(QDialog):
    """Collect a validated ``TaskDraft`` without touching the database."""

    def __init__(
        self,
        task: object | None = None,
        available_tasks: Iterable[object] = (),
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self.setWindowTitle("작업 수정" if task is not None else "작업 추가")
        self.setMinimumSize(620, 650)

        self.title_edit = QLineEdit()
        self.title_edit.setAccessibleName("작업 제목")
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setAccessibleName("작업 프롬프트")
        self.prompt_edit.setPlaceholderText("AI에게 맡길 작업을 구체적으로 입력하세요.")

        self.path_edit = QLineEdit()
        self.path_edit.setAccessibleName("프로젝트 경로")
        browse_button = QPushButton("찾아보기…")
        browse_button.clicked.connect(self._browse_path)
        path_row = QWidget()
        path_layout = QHBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.addWidget(self.path_edit, 1)
        path_layout.addWidget(browse_button)

        self.agent_combo = QComboBox()
        for label, policy in (
            ("Any", AgentPolicy.ANY),
            ("Claude", AgentPolicy.CLAUDE),
            ("Codex", AgentPolicy.CODEX),
        ):
            self.agent_combo.addItem(label, policy)

        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(0, 100)
        self.priority_spin.setValue(50)

        self.approval_combo = QComboBox()
        self.approval_combo.addItem("승인 후 실행", ApprovalMode.CONFIRM)
        self.approval_combo.addItem("자동 실행", ApprovalMode.AUTO)

        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(0, 20)
        self.max_retries_spin.setValue(2)
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(0, 24 * 60)
        self.retry_delay_spin.setValue(10)
        self.retry_delay_spin.setSuffix("분")

        self.not_before_check = QCheckBox("실행 가능 시각 지정")
        self.not_before_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.not_before_edit.setCalendarPopup(True)
        self.not_before_edit.setDisplayFormat("yyyy-MM-dd HH:mm")
        self.not_before_edit.setEnabled(False)
        self.not_before_check.toggled.connect(self.not_before_edit.setEnabled)
        not_before_row = QWidget()
        not_before_layout = QHBoxLayout(not_before_row)
        not_before_layout.setContentsMargins(0, 0, 0, 0)
        not_before_layout.addWidget(self.not_before_check)
        not_before_layout.addWidget(self.not_before_edit, 1)

        self.tags_edit = QLineEdit()
        self.tags_edit.setPlaceholderText("쉼표로 구분")

        self.dependencies_list = QListWidget()
        self.dependencies_list.setAccessibleName("선행 작업")
        self.dependencies_list.setMinimumHeight(120)
        self._populate_dependencies(available_tasks)

        form = QFormLayout()
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.ExpandingFieldsGrow)
        form.addRow("제목 *", self.title_edit)
        form.addRow("프롬프트 *", self.prompt_edit)
        form.addRow("프로젝트 경로 *", path_row)
        form.addRow("담당 AI", self.agent_combo)
        form.addRow("우선순위", self.priority_spin)
        form.addRow("실행 방식", self.approval_combo)
        form.addRow("최대 재시도", self.max_retries_spin)
        form.addRow("재시도 간격", self.retry_delay_spin)
        form.addRow("예약", not_before_row)
        form.addRow("태그", self.tags_edit)
        form.addRow(QLabel("선행 작업"), self.dependencies_list)

        self.button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_button = self.button_box.button(QDialogButtonBox.StandardButton.Save)
        save_button.setText("저장")
        self.button_box.button(QDialogButtonBox.StandardButton.Cancel).setText("취소")
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)
        layout.addLayout(form)
        layout.addWidget(self.button_box)

        if task is not None:
            self._load_task(task)
        self.title_edit.setFocus()

    def _populate_dependencies(self, available_tasks: Iterable[object]) -> None:
        current_id = getattr(self._task, "id", None)
        for dependency in available_tasks:
            dependency_id = getattr(dependency, "id", None)
            if dependency_id is None or dependency_id == current_id:
                continue
            item = QListWidgetItem(
                f"#{dependency_id}  {getattr(dependency, 'title', '')}",
                self.dependencies_list,
            )
            item.setData(Qt.ItemDataRole.UserRole, dependency_id)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)

    def _load_task(self, task: object) -> None:
        self.title_edit.setText(str(getattr(task, "title", "")))
        self.prompt_edit.setPlainText(str(getattr(task, "prompt", "")))
        self.path_edit.setText(str(getattr(task, "project_path", "")))
        self._select_data(self.agent_combo, enum_value(getattr(task, "agent_policy", "any")))
        self.priority_spin.setValue(int(getattr(task, "priority", 50)))
        self._select_data(
            self.approval_combo,
            enum_value(getattr(task, "approval_mode", ApprovalMode.CONFIRM)),
        )
        self.max_retries_spin.setValue(int(getattr(task, "max_retries", 2)))
        self.retry_delay_spin.setValue(int(getattr(task, "retry_delay_minutes", 10)))
        not_before = getattr(task, "not_before", None)
        if not_before is not None:
            self.not_before_check.setChecked(True)
            self.not_before_edit.setDateTime(QDateTime(_utc_naive_to_local(not_before)))
        tags = getattr(task, "tags", ()) or ()
        self.tags_edit.setText(", ".join(tags))
        selected_ids = set(getattr(task, "dependency_ids", ()) or ())
        if not selected_ids:
            selected_ids = {
                dependency.id for dependency in getattr(task, "depends_on", ()) if dependency.id
            }
        for row in range(self.dependencies_list.count()):
            item = self.dependencies_list.item(row)
            if item.data(Qt.ItemDataRole.UserRole) in selected_ids:
                item.setCheckState(Qt.CheckState.Checked)

    @staticmethod
    def _select_data(combo: QComboBox, value: str) -> None:
        for index in range(combo.count()):
            if enum_value(combo.itemData(index)) == value:
                combo.setCurrentIndex(index)
                return

    def _browse_path(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "프로젝트 폴더 선택",
            self.path_edit.text(),
        )
        if selected:
            self.path_edit.setText(selected)

    def selected_dependency_ids(self) -> list[int]:
        return [
            int(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.dependencies_list.count())
            if (item := self.dependencies_list.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def build_draft(self) -> TaskDraft:
        title = self.title_edit.text().strip()
        prompt = self.prompt_edit.toPlainText().strip()
        project_path = self.path_edit.text().strip()
        missing = [
            label
            for label, value in (
                ("제목", title),
                ("프롬프트", prompt),
                ("프로젝트 경로", project_path),
            )
            if not value
        ]
        if missing:
            raise ValueError(f"필수 입력: {', '.join(missing)}")
        not_before = (
            _local_to_utc_naive(self.not_before_edit.dateTime().toPython())
            if self.not_before_check.isChecked()
            else None
        )
        return TaskDraft(
            title=title,
            prompt=prompt,
            project_path=project_path,
            agent_policy=self.agent_combo.currentData(),
            priority=self.priority_spin.value(),
            approval_mode=self.approval_combo.currentData(),
            max_retries=self.max_retries_spin.value(),
            retry_delay_minutes=self.retry_delay_spin.value(),
            not_before=not_before,
            tags=[tag.strip() for tag in self.tags_edit.text().split(",") if tag.strip()],
            dependency_ids=self.selected_dependency_ids(),
        )

    def draft(self) -> TaskDraft:
        return self.build_draft()

    def accept(self) -> None:
        try:
            self.build_draft()
        except (ValueError, ValidationError) as exc:
            QMessageBox.warning(self, "입력 확인", str(exc))
            return
        super().accept()
