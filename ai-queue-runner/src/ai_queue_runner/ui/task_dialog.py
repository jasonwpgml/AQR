"""Task create/edit dialog."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QShowEvent
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
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import AgentPolicy, ApprovalMode, TaskCreator
from ai_queue_runner.ui.task_table import enum_value

EMPTY_ROOTS_MESSAGE = "설정에서 허용 프로젝트 루트를 먼저 추가하세요."
OUTSIDE_ROOTS_MESSAGE = "선택한 폴더는 허용된 프로젝트 루트 밖에 있습니다."
STALE_PATH_MESSAGE = (
    "기존 프로젝트 경로가 현재 허용 루트 밖에 있습니다. 새 경로를 선택하세요."
)
INVALID_ROOT_MESSAGE = "선택한 허용 프로젝트 루트가 존재하지 않습니다. 설정을 확인하세요."


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
        config: AppConfig,
        prompt_template_service: object | None = None,
        preset_not_before: datetime | None = None,
    ) -> None:
        super().__init__(parent)
        self._task = task
        self._config = config
        self._selected_project_path: Path | None = None
        self._created_by: TaskCreator | None = None
        self._creator_session_id: str | None = None
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

        self.root_combo = QComboBox()
        self.root_combo.setAccessibleName("허용 프로젝트 루트")
        self.root_combo.setEditable(False)
        self.subfolder_button = QPushButton("하위 폴더 선택…")
        self.subfolder_button.clicked.connect(self._browse_path)
        self.path_display = QLineEdit()
        self.path_display.setAccessibleName("프로젝트 경로")
        self.path_display.setReadOnly(True)
        self.path_warning_label = QLabel()
        self.path_warning_label.setWordWrap(True)
        self.path_warning_label.setStyleSheet("color: #b42318;")
        root_controls = QHBoxLayout()
        root_controls.setContentsMargins(0, 0, 0, 0)
        root_controls.addWidget(self.root_combo, 1)
        root_controls.addWidget(self.subfolder_button)
        path_row = QWidget()
        path_layout = QVBoxLayout(path_row)
        path_layout.setContentsMargins(0, 0, 0, 0)
        path_layout.setSpacing(4)
        path_layout.addLayout(root_controls)
        path_layout.addWidget(self.path_display)
        path_layout.addWidget(self.path_warning_label)
        self.root_combo.currentIndexChanged.connect(self._root_selected)

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

        self.estimated_tokens_spin = QSpinBox()
        self.estimated_tokens_spin.setRange(0, 100_000_000)
        self.estimated_tokens_spin.setSingleStep(1_000)
        self.estimated_tokens_spin.setSpecialValueText("미지정")

        self.approval_combo = QComboBox()
        self.approval_combo.addItem("승인 후 실행", ApprovalMode.CONFIRM)
        self.approval_combo.addItem("자동 실행", ApprovalMode.AUTO)

        self.auto_commit_check = QCheckBox("성공 후 자동 Git 커밋")
        self.auto_commit_check.setToolTip(
            "scheduler.auto_commit_enabled 설정도 켜져 있어야 실행됩니다."
        )
        self.draft_pr_check = QCheckBox("자동 커밋 후 draft PR 생성")
        self.draft_pr_check.setToolTip(
            "자동 커밋과 scheduler.draft_pr_enabled가 필요하며, "
            "관리형 worktree 실행에서만 push합니다."
        )

        self.max_retries_spin = QSpinBox()
        self.max_retries_spin.setRange(0, 20)
        self.max_retries_spin.setValue(2)
        self.retry_delay_spin = QSpinBox()
        self.retry_delay_spin.setRange(0, 24 * 60)
        self.retry_delay_spin.setValue(10)
        self.retry_delay_spin.setSuffix("분")

        self.recurrence_combo = QComboBox()
        self.recurrence_combo.setAccessibleName("반복 규칙")
        self.recurrence_combo.addItem("반복 안 함", None)
        self.recurrence_combo.addItem("매일", 1)
        self.recurrence_combo.addItem("매주", 7)
        self.recurrence_combo.addItem("N일 간격", "custom")
        self.recurrence_days_spin = QSpinBox()
        self.recurrence_days_spin.setAccessibleName("반복 간격 일수")
        self.recurrence_days_spin.setRange(2, 36_500)
        self.recurrence_days_spin.setValue(2)
        self.recurrence_days_spin.setSuffix("일")
        self.recurrence_days_spin.setEnabled(False)
        self.recurrence_combo.currentIndexChanged.connect(self._recurrence_selected)
        recurrence_row = QWidget()
        recurrence_layout = QHBoxLayout(recurrence_row)
        recurrence_layout.setContentsMargins(0, 0, 0, 0)
        recurrence_layout.addWidget(self.recurrence_combo, 1)
        recurrence_layout.addWidget(self.recurrence_days_spin)

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
        form.addRow("예상 토큰", self.estimated_tokens_spin)
        form.addRow("실행 방식", self.approval_combo)
        form.addRow("Git", self.auto_commit_check)
        form.addRow("GitHub", self.draft_pr_check)
        form.addRow("최대 재시도", self.max_retries_spin)
        form.addRow("재시도 간격", self.retry_delay_spin)
        form.addRow("반복", recurrence_row)
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
        else:
            self._refresh_project_roots()
            if preset_not_before is not None:
                self.not_before_check.setChecked(True)
                self.not_before_edit.setDateTime(
                    QDateTime(preset_not_before.astimezone())
                )
        self.title_edit.setFocus()

    def showEvent(self, event: QShowEvent) -> None:
        self._refresh_project_roots()
        super().showEvent(event)

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
        self._created_by = TaskCreator(
            enum_value(getattr(task, "created_by", TaskCreator.USER))
        )
        self._creator_session_id = getattr(task, "creator_session_id", None)
        self.title_edit.setText(str(getattr(task, "title", "")))
        self.prompt_edit.setPlainText(str(getattr(task, "prompt", "")))
        self._refresh_project_roots(str(getattr(task, "project_path", "")))
        self._select_data(self.agent_combo, enum_value(getattr(task, "agent_policy", "any")))
        self.priority_spin.setValue(int(getattr(task, "priority", 50)))
        self.estimated_tokens_spin.setValue(int(getattr(task, "estimated_tokens", 0)))
        self._select_data(
            self.approval_combo,
            enum_value(getattr(task, "approval_mode", ApprovalMode.CONFIRM)),
        )
        self.auto_commit_check.setChecked(bool(getattr(task, "auto_commit", False)))
        self.draft_pr_check.setChecked(bool(getattr(task, "draft_pr", False)))
        self.max_retries_spin.setValue(int(getattr(task, "max_retries", 2)))
        self.retry_delay_spin.setValue(int(getattr(task, "retry_delay_minutes", 10)))
        recurrence_interval_days = getattr(task, "recurrence_interval_days", None)
        if recurrence_interval_days in {1, 7}:
            self.recurrence_combo.setCurrentIndex(
                self.recurrence_combo.findData(recurrence_interval_days)
            )
        elif recurrence_interval_days is not None:
            self.recurrence_days_spin.setValue(int(recurrence_interval_days))
            self.recurrence_combo.setCurrentIndex(
                self.recurrence_combo.findData("custom")
            )
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

    def _recurrence_selected(self, _index: int) -> None:
        self.recurrence_days_spin.setEnabled(
            self.recurrence_combo.currentData() == "custom"
        )

    def _configured_roots(self) -> list[Path]:
        return [Path(root) for root in self._config.app.allowed_project_roots]

    @staticmethod
    def _resolve_existing_directory(value: Path | str) -> Path | None:
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            return None
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            return None
        return resolved if resolved.is_dir() else None

    def _containing_root_index(self, candidate: Path) -> int | None:
        for index, root in enumerate(self._configured_roots()):
            resolved_root = self._resolve_existing_directory(root)
            if resolved_root is not None and (
                candidate == resolved_root or resolved_root in candidate.parents
            ):
                return index
        return None

    def _set_path_warning(self, message: str) -> None:
        self.path_warning_label.setText(message)
        self.path_warning_label.setVisible(bool(message))

    def _update_path_controls(self, *, roots_available: bool) -> None:
        self.root_combo.setEnabled(roots_available)
        self.path_display.setEnabled(roots_available)
        self.subfolder_button.setEnabled(
            roots_available
            and self.root_combo.currentIndex() >= 0
            and self._selected_project_path is not None
        )

    def _refresh_project_roots(self, project_path: str | None = None) -> None:
        roots = self._configured_roots()
        displayed_path = self.path_display.text().strip() if project_path is None else project_path

        self.root_combo.blockSignals(True)
        self.root_combo.clear()
        for root in roots:
            self.root_combo.addItem(str(root), str(root))

        if not roots:
            self.root_combo.setCurrentIndex(-1)
            self.root_combo.blockSignals(False)
            self._selected_project_path = None
            self.path_display.setText(displayed_path)
            self._set_path_warning(EMPTY_ROOTS_MESSAGE)
            self._update_path_controls(roots_available=False)
            return

        candidate = self._resolve_existing_directory(displayed_path) if displayed_path else None
        containing_index = self._containing_root_index(candidate) if candidate is not None else None
        if containing_index is not None:
            self.root_combo.setCurrentIndex(containing_index)
            self._selected_project_path = candidate
            self.path_display.setText(str(candidate))
            self._set_path_warning("")
        elif displayed_path:
            self.root_combo.setCurrentIndex(-1)
            self._selected_project_path = None
            self.path_display.setText(displayed_path)
            self._set_path_warning(STALE_PATH_MESSAGE)
        else:
            self.root_combo.setCurrentIndex(0)
            self._selected_project_path = None
        self.root_combo.blockSignals(False)

        if not displayed_path:
            self._root_selected(0)
        else:
            self._update_path_controls(roots_available=True)

    def _root_selected(self, index: int) -> None:
        if index < 0:
            self._selected_project_path = None
            self._update_path_controls(roots_available=self.root_combo.count() > 0)
            return
        root = self._resolve_existing_directory(str(self.root_combo.itemData(index)))
        if root is None:
            self._selected_project_path = None
            self.path_display.setText(str(self.root_combo.itemData(index)))
            self._set_path_warning(INVALID_ROOT_MESSAGE)
        else:
            self._selected_project_path = root
            self.path_display.setText(str(root))
            self._set_path_warning("")
        self._update_path_controls(roots_available=True)

    def _browse_path(self) -> None:
        root_index = self.root_combo.currentIndex()
        if root_index < 0:
            return
        selected_root = self._resolve_existing_directory(
            str(self.root_combo.itemData(root_index))
        )
        if selected_root is None:
            return
        selected = QFileDialog.getExistingDirectory(
            self,
            "하위 폴더 선택",
            str(selected_root),
        )
        if not selected:
            return
        candidate = self._resolve_existing_directory(selected)
        containing_index = self._containing_root_index(candidate) if candidate is not None else None
        if candidate is None or containing_index is None:
            QMessageBox.warning(self, "프로젝트 경로 확인", OUTSIDE_ROOTS_MESSAGE)
            return
        self.root_combo.blockSignals(True)
        self.root_combo.setCurrentIndex(containing_index)
        self.root_combo.blockSignals(False)
        self._selected_project_path = candidate
        self.path_display.setText(str(candidate))
        self._set_path_warning("")
        self._update_path_controls(roots_available=True)

    def selected_dependency_ids(self) -> list[int]:
        return [
            int(item.data(Qt.ItemDataRole.UserRole))
            for row in range(self.dependencies_list.count())
            if (item := self.dependencies_list.item(row)).checkState() == Qt.CheckState.Checked
        ]

    def build_draft(self) -> TaskDraft:
        title = self.title_edit.text().strip()
        prompt = self.prompt_edit.toPlainText().strip()
        if not self._configured_roots():
            raise ValueError(EMPTY_ROOTS_MESSAGE)
        project_path = (
            self._resolve_existing_directory(self._selected_project_path)
            if self._selected_project_path is not None
            else None
        )
        if project_path is None or self._containing_root_index(project_path) is None:
            raise ValueError("프로젝트 경로를 허용 루트 안에서 다시 선택하세요.")
        missing = [
            label
            for label, value in (
                ("제목", title),
                ("프롬프트", prompt),
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
        recurrence_value = self.recurrence_combo.currentData()
        recurrence_interval_days = (
            self.recurrence_days_spin.value()
            if recurrence_value == "custom"
            else recurrence_value
        )
        return TaskDraft(
            title=title,
            prompt=prompt,
            project_path=str(project_path),
            created_by=self._created_by,
            creator_session_id=self._creator_session_id,
            agent_policy=self.agent_combo.currentData(),
            priority=self.priority_spin.value(),
            estimated_tokens=self.estimated_tokens_spin.value(),
            approval_mode=self.approval_combo.currentData(),
            auto_commit=self.auto_commit_check.isChecked(),
            draft_pr=self.draft_pr_check.isChecked(),
            max_retries=self.max_retries_spin.value(),
            retry_delay_minutes=self.retry_delay_spin.value(),
            recurrence_interval_days=recurrence_interval_days,
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
