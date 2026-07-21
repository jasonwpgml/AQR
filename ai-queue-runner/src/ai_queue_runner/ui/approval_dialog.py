"""Human confirmation before launching a CLI task."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence

from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.ui.task_table import enum_value


class ApprovalDialog(QDialog):
    """Show the exact execution boundary and return approve/reject."""

    def __init__(
        self,
        task: object,
        adapter: object | None = None,
        *,
        command: Sequence[str] | None = None,
        stdin_text: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("실행 승인")
        self.setMinimumSize(700, 520)

        execution_path = str(
            getattr(task, "worktree_path", None) or getattr(task, "project_path", "")
        )
        execution_task = _TaskPathOverride(task, execution_path)
        if command is None and adapter is not None:
            command = adapter.build_command(execution_task)
        if stdin_text is None and adapter is not None:
            stdin_text = adapter.build_stdin(execution_task)
        command = list(command or ())
        if stdin_text is None:
            stdin_text = str(getattr(task, "prompt", ""))

        self.title_value = QLineEdit(str(getattr(task, "title", "")))
        assigned = getattr(task, "assigned_agent", None) or getattr(task, "agent_policy", "")
        self.agent_value = QLineEdit(enum_value(assigned).title())
        self.path_value = QLineEdit(execution_path)
        self.priority_value = QLineEdit(str(getattr(task, "priority", "")))
        self.command_view = QPlainTextEdit(subprocess.list2cmdline(command))
        self.prompt_view = QPlainTextEdit(stdin_text)
        for widget in (
            self.title_value,
            self.agent_value,
            self.path_value,
            self.priority_value,
        ):
            widget.setReadOnly(True)
        self.command_view.setReadOnly(True)
        self.prompt_view.setReadOnly(True)
        fixed_font = self.command_view.font()
        fixed_font.setFamily("Consolas")
        self.command_view.setFont(fixed_font)
        self.prompt_view.setFont(fixed_font)

        warning = QLabel("아래 명령이 지정된 프로젝트에서 실행됩니다. 내용을 확인하세요.")
        warning.setWordWrap(True)
        form = QFormLayout()
        form.addRow("작업", self.title_value)
        form.addRow("담당 AI", self.agent_value)
        form.addRow("프로젝트 경로", self.path_value)
        form.addRow("우선순위", self.priority_value)
        form.addRow("실행 명령", self.command_view)
        form.addRow("실행 프롬프트 (stdin)", self.prompt_view)

        self.button_box = QDialogButtonBox()
        self.approve_button = self.button_box.addButton(
            "승인하고 실행", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.reject_button = self.button_box.addButton(
            "거부", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.addWidget(warning)
        layout.addLayout(form, 1)
        layout.addWidget(self.button_box)

    @property
    def command_text(self) -> str:
        return self.command_view.toPlainText()

    @property
    def prompt_text(self) -> str:
        return self.prompt_view.toPlainText()


class _TaskPathOverride:
    def __init__(self, task: object, project_path: str) -> None:
        self._task = task
        self.project_path = project_path

    def __getattr__(self, name: str) -> object:
        return getattr(self._task, name)
