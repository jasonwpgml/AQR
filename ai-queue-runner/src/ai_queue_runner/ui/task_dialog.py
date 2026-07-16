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
    QInputDialog,
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
    """Collect a validated ``TaskDraft`` and manage independent prompt templates."""

    def __init__(
        self,
        task: object | None = None,
        available_tasks: Iterable[object] = (),
        parent: QWidget | None = None,
        *,
        prompt_template_service: object | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self._prompt_template_service = prompt_template_service
        self._templates_by_name: dict[str, object] = {}
        self._selected_template_name: str | None = None
        self.setWindowTitle("작업 수정" if task is not None else "작업 추가")
        self.setMinimumSize(620, 650)

        self.title_edit = QLineEdit()
        self.title_edit.setAccessibleName("작업 제목")
        self.prompt_edit = QPlainTextEdit()
        self.prompt_edit.setAccessibleName("작업 프롬프트")
        self.prompt_edit.setPlaceholderText("AI에게 맡길 작업을 구체적으로 입력하세요.")

        self.template_combo = QComboBox()
        self.template_combo.setAccessibleName("프롬프트 템플릿")
        self.save_template_button = QPushButton("템플릿으로 저장")
        self.delete_template_button = QPushButton("삭제")
        self.delete_template_button.setMaximumWidth(60)
        self.template_combo.currentIndexChanged.connect(self._template_selected)
        self.save_template_button.clicked.connect(self._save_prompt_template)
        self.delete_template_button.clicked.connect(self._delete_prompt_template)
        template_controls = QHBoxLayout()
        template_controls.setContentsMargins(0, 0, 0, 0)
        template_controls.addWidget(self.template_combo, 1)
        template_controls.addWidget(self.save_template_button)
        template_controls.addWidget(self.delete_template_button)
        prompt_row = QWidget()
        prompt_layout = QVBoxLayout(prompt_row)
        prompt_layout.setContentsMargins(0, 0, 0, 0)
        prompt_layout.setSpacing(4)
        prompt_layout.addLayout(template_controls)
        prompt_layout.addWidget(self.prompt_edit)
        self._refresh_templates()

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
        form.addRow("프롬프트 *", prompt_row)
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

    def _refresh_templates(self, selected_name: str | None = None) -> None:
        enabled = self._prompt_template_service is not None
        templates: list[object] = []
        if enabled:
            try:
                templates = list(self._prompt_template_service.list())
            except Exception as exc:
                QMessageBox.warning(self, "템플릿 불러오기 실패", str(exc))
                enabled = False

        self._templates_by_name = {str(template.name): template for template in templates}
        target_name = selected_name or self._selected_template_name
        self.template_combo.blockSignals(True)
        self.template_combo.clear()
        self.template_combo.addItem("템플릿 선택…", None)
        for template in templates:
            name = str(template.name)
            self.template_combo.addItem(name, name)
        target_index = self.template_combo.findData(target_name)
        self.template_combo.setCurrentIndex(max(0, target_index))
        self.template_combo.blockSignals(False)

        self._selected_template_name = target_name if target_index >= 0 else None
        self.template_combo.setEnabled(enabled)
        self.save_template_button.setEnabled(enabled)
        self.delete_template_button.setEnabled(enabled and self._selected_template_name is not None)

    def _template_selected(self, _index: int) -> None:
        selected_name = self.template_combo.currentData()
        if selected_name is None:
            self._selected_template_name = None
            self.delete_template_button.setEnabled(False)
            return
        template = self._templates_by_name[str(selected_name)]
        template_prompt = str(template.prompt)
        current_prompt = self.prompt_edit.toPlainText()
        if current_prompt.strip() and current_prompt != template_prompt:
            answer = QMessageBox.question(
                self,
                "프롬프트 바꾸기",
                "현재 프롬프트를 선택한 템플릿 내용으로 바꾸시겠습니까?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                self._restore_template_selection()
                return
        self.prompt_edit.setPlainText(template_prompt)
        self._selected_template_name = str(selected_name)
        self.delete_template_button.setEnabled(True)

    def _restore_template_selection(self) -> None:
        index = self.template_combo.findData(self._selected_template_name)
        self.template_combo.blockSignals(True)
        self.template_combo.setCurrentIndex(max(0, index))
        self.template_combo.blockSignals(False)

    def _save_prompt_template(self) -> None:
        if self._prompt_template_service is None:
            return
        name, accepted = QInputDialog.getText(
            self,
            "프롬프트 템플릿 저장",
            "템플릿 이름:",
            QLineEdit.EchoMode.Normal,
            self._selected_template_name or "",
        )
        if not accepted:
            return
        try:
            saved = self._prompt_template_service.save(name, self.prompt_edit.toPlainText())
        except Exception as exc:
            QMessageBox.warning(self, "템플릿 저장 실패", str(exc))
            return
        self._refresh_templates(str(saved.name))

    def _delete_prompt_template(self) -> None:
        if self._prompt_template_service is None or self._selected_template_name is None:
            return
        name = self._selected_template_name
        if (
            QMessageBox.question(
                self,
                "프롬프트 템플릿 삭제",
                f"'{name}' 템플릿을 삭제하시겠습니까?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self._prompt_template_service.delete(name)
        except Exception as exc:
            QMessageBox.warning(self, "템플릿 삭제 실패", str(exc))
            return
        self._selected_template_name = None
        self._refresh_templates()

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
