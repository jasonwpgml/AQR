"""Headless smoke tests for the native desktop UI."""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    TaskStatus,
)
from ai_queue_runner.ui.approval_dialog import ApprovalDialog
from ai_queue_runner.ui.log_viewer import LogViewer
from ai_queue_runner.ui.main_window import MainWindow
from ai_queue_runner.ui.task_dialog import TaskDialog
from ai_queue_runner.ui.task_table import TaskColumn, TaskTableModel
from ai_queue_runner.ui.tray import QueueSystemTray


@pytest.fixture(scope="session")
def app() -> QApplication:
    return QApplication.instance() or QApplication([])


def make_task(task_id: int = 1, **overrides):
    values = {
        "id": task_id,
        "title": "테스트 작성",
        "prompt": "회귀 테스트를 추가한다.",
        "project_path": "C:/work/sample",
        "agent_policy": AgentPolicy.CODEX,
        "assigned_agent": None,
        "priority": 70,
        "status": TaskStatus.WAITING,
        "approval_mode": ApprovalMode.CONFIRM,
        "max_retries": 2,
        "retry_count": 0,
        "retry_delay_minutes": 10,
        "not_before": None,
        "tags": [],
        "dependency_ids": [],
        "last_error": None,
        "archived": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_task_table_model_exposes_compact_korean_projection(app):
    model = TaskTableModel([make_task()])

    assert model.rowCount() == 1
    assert model.columnCount() == 5
    assert model.data(model.index(0, TaskColumn.PRIORITY)) == 70
    assert model.data(model.index(0, TaskColumn.AGENT)) == "Codex"
    assert model.data(model.index(0, TaskColumn.STATUS)) == "대기"
    assert model.data(model.index(0, TaskColumn.PROJECT)) == "sample"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole).id == 1


def test_task_dialog_validates_and_builds_draft_with_dependencies(app):
    dependency = make_task(2, title="스키마 작성")
    dialog = TaskDialog(available_tasks=[dependency])

    with pytest.raises(ValueError, match="필수 입력"):
        dialog.build_draft()

    dialog.title_edit.setText("UI 연결")
    dialog.prompt_edit.setPlainText("작업 목록을 연결한다.")
    dialog.path_edit.setText("C:/work/sample")
    dialog.dependencies_list.item(0).setCheckState(Qt.CheckState.Checked)
    draft = dialog.build_draft()

    assert draft.title == "UI 연결"
    assert draft.agent_policy is AgentPolicy.ANY
    assert draft.approval_mode is ApprovalMode.CONFIRM
    assert draft.dependency_ids == [2]


def test_approval_dialog_shows_exact_execution_inputs(app):
    task = make_task(assigned_agent=AgentName.CODEX)
    command = ["codex", "exec", "--cd", task.project_path, "-"]
    dialog = ApprovalDialog(task, command=command, stdin_text="wrapped prompt")

    assert dialog.path_value.text() == task.project_path
    assert dialog.priority_value.text() == "70"
    assert "codex exec" in dialog.command_text
    assert task.project_path in dialog.command_text
    assert dialog.prompt_text == "wrapped prompt"


class FakeTaskService:
    def __init__(self, tasks):
        self.tasks = tasks

    def list(self, status=None):
        if status is None:
            return self.tasks
        return [task for task in self.tasks if task.status is status]

    def get(self, task_id):
        return next((task for task in self.tasks if task.id == task_id), None)

    def list_runs(self, task_id):
        return []


class FakeAgentService:
    def __init__(self):
        self.states = [
            SimpleNamespace(
                agent=agent,
                status=AgentStatus.AVAILABLE,
                available_at=None,
                current_task_id=None,
                last_error=None,
            )
            for agent in (AgentName.CLAUDE, AgentName.CODEX)
        ]

    def list(self):
        return self.states


class FakeRuntime:
    running = False
    paused = True

    def __init__(self):
        self.reserved_now = []

    def start(self):
        self.running = True
        self.paused = False

    def pause(self):
        self.paused = True

    def is_active(self, task_id=None):
        return False

    def reserve_now(self, task_id):
        self.reserved_now.append(task_id)
        return True


def test_main_window_constructs_and_refreshes_from_services(app):
    task = make_task()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        create_tray=False,
    )
    app.processEvents()

    assert window.task_model.rowCount() == 1
    assert window.agent_widgets[AgentName.CLAUDE].status_label.text() == "사용 가능"
    window.task_table.selectRow(0)
    app.processEvents()
    assert "테스트 작성" in window.details_view.toPlainText()
    assert window.edit_button.isEnabled()
    window.start_scheduler()
    assert window.pause_button.isEnabled()

    window.close()


@pytest.mark.parametrize("approval_mode", [ApprovalMode.CONFIRM, ApprovalMode.AUTO])
def test_main_window_polls_each_pending_reservation_once(app, approval_mode):
    task = make_task(
        status=TaskStatus.RESERVED,
        approval_mode=approval_mode,
        assigned_agent=AgentName.CODEX,
    )
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        create_tray=False,
    )
    window.refresh_timer.stop()
    prompted = []
    window.request_approval = lambda task_id: prompted.append(task_id) or True

    window._poll_runtime()
    window._poll_runtime()

    assert prompted == [task.id]
    window.close()


def test_task_table_orders_priority_and_labels_archive(app):
    model = TaskTableModel(
        [
            make_task(1, priority=10),
            make_task(2, priority=90, status=TaskStatus.SUCCEEDED, archived=True),
        ]
    )

    assert model.data(model.index(0, TaskColumn.PRIORITY)) == 90
    assert model.data(model.index(0, TaskColumn.STATUS)) == "보관됨"


def test_main_window_exposes_required_task_management_actions(app, monkeypatch):
    task = make_task()
    runtime = FakeRuntime()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        runtime,
        adapters={},
        create_tray=False,
    )
    window.refresh_timer.stop()
    window.task_table.selectRow(0)
    app.processEvents()
    prompted = []
    window.request_approval = lambda task_id: prompted.append(task_id) or True
    opened = []
    monkeypatch.setattr(
        "ai_queue_runner.ui.main_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )

    assert window.run_now_button.isEnabled()
    assert window.move_top_button.isEnabled()
    assert window.open_folder_button.isEnabled()
    window.run_now_task()
    window.open_project_folder()
    window.open_log()

    assert runtime.reserved_now == [task.id]
    assert prompted == [task.id]
    assert opened == [task.project_path]
    assert window.log_viewer.tabs.currentWidget() is window.log_viewer.stdout_view
    window.close()


def test_main_window_reports_automatic_run_lifecycle(app):
    task = make_task()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        create_tray=False,
    )
    window.refresh_timer.stop()

    task.status = TaskStatus.RUNNING
    window._poll_runtime()
    assert "실행을 시작" in window.statusBar().currentMessage()

    task.status = TaskStatus.SUCCEEDED
    window._poll_runtime()
    assert "실행을 완료" in window.statusBar().currentMessage()
    window.close()


def test_log_viewer_bounds_visible_text_but_preserves_file(app, tmp_path):
    log_path = tmp_path / "large.log"
    content = "x" * (3 * 1024 * 1024)
    log_path.write_text(content, encoding="utf-8")
    viewer = LogViewer()
    viewer.stop_polling()

    viewer.set_run(
        SimpleNamespace(
            attempt=1,
            agent=AgentName.CODEX,
            exit_code=None,
            stdout_path=str(log_path),
            stderr_path=None,
        )
    )

    visible = viewer.stdout_view.toPlainText()
    assert visible.startswith("[이전 로그는 생략되었습니다.")
    assert len(visible) < len(content)
    assert log_path.stat().st_size == len(content)


def test_tray_notification_falls_back_when_system_tray_is_unavailable(app, monkeypatch):
    messages = []
    tray = QueueSystemTray(fallback=messages.append)
    monkeypatch.setattr(tray, "isSystemTrayAvailable", lambda: False)

    tray.notify("AI Queue Runner", "작업 완료")

    assert messages == ["AI Queue Runner: 작업 완료"]
