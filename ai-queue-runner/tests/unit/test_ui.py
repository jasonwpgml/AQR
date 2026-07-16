"""Headless smoke tests for the native desktop UI."""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QMessageBox

from ai_queue_runner.application.prompt_template_service import PromptTemplateService
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.exporter import export_config
from ai_queue_runner.config.loader import load_config
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    TaskStatus,
)
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.ui.approval_dialog import ApprovalDialog
from ai_queue_runner.ui.log_viewer import LogViewer
from ai_queue_runner.ui.main_window import MainWindow
from ai_queue_runner.ui.settings_dialog import SettingsDialog
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


def test_task_dialog_saves_selects_and_deletes_prompt_templates(app, tmp_path, monkeypatch):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    service = PromptTemplateService(make_session_factory(engine))
    service.save("Existing", "Original prompt")
    service.save("Other", "Other prompt")
    dialog = TaskDialog(prompt_template_service=service)

    existing_index = dialog.template_combo.findData("Existing")
    dialog.template_combo.setCurrentIndex(existing_index)
    assert dialog.prompt_edit.toPlainText() == "Original prompt"

    requested_defaults = []

    def template_name_input(*args):
        requested_defaults.append(args[4])
        return "Existing", True

    monkeypatch.setattr(
        "ai_queue_runner.ui.task_dialog.QInputDialog.getText",
        template_name_input,
    )
    dialog.prompt_edit.setPlainText("Updated prompt")
    dialog.save_template_button.click()

    assert requested_defaults == ["Existing"]
    assert service.list()[0].prompt == "Updated prompt"
    assert dialog.template_combo.findData("Existing") >= 0

    confirmations = []

    def confirm(*args):
        confirmations.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("ai_queue_runner.ui.task_dialog.QMessageBox.question", confirm)
    dialog.prompt_edit.setPlainText("Unsaved editor text")
    dialog.template_combo.setCurrentIndex(dialog.template_combo.findData("Other"))

    assert dialog.prompt_edit.toPlainText() == "Other prompt"
    assert confirmations == ["현재 프롬프트를 선택한 템플릿 내용으로 바꾸시겠습니까?"]

    dialog.delete_template_button.click()

    assert dialog.template_combo.findData("Other") == -1
    assert [template.name for template in service.list()] == ["Existing"]
    assert confirmations[-1] == "'Other' 템플릿을 삭제하시겠습니까?"
    dialog.close()
    engine.dispose()


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
        config=AppConfig(),
        active_config_path=Path("config.toml"),
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
        config=AppConfig(),
        active_config_path=Path("config.toml"),
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
    config = AppConfig()
    active_config_path = Path("config.toml")
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        runtime,
        adapters={},
        config=config,
        active_config_path=active_config_path,
        create_tray=False,
    )
    window.refresh_timer.stop()
    window.task_table.selectRow(0)
    app.processEvents()
    prompted = []
    window.request_approval = lambda task_id: prompted.append(task_id) or True
    opened = []
    opened_settings = []

    class FakeSettingsDialog:
        def __init__(self, received_config, received_path, parent=None):
            opened_settings.append((received_config, received_path, parent))

        def exec(self):
            return 0

    monkeypatch.setattr("ai_queue_runner.ui.main_window.SettingsDialog", FakeSettingsDialog)
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
    window.settings_button.click()

    assert runtime.reserved_now == [task.id]
    assert prompted == [task.id]
    assert opened == [task.project_path]
    assert opened_settings == [(config, active_config_path, window)]
    assert window.log_viewer.tabs.currentWidget() is window.log_viewer.stdout_view
    window.close()


def test_main_window_reports_automatic_run_lifecycle(app):
    task = make_task()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
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


def test_settings_dialog_lists_existing_allowed_roots(app, tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    config = AppConfig()
    config.app.allowed_project_roots[:] = [first_root, second_root]

    dialog = SettingsDialog(config, tmp_path / "config.toml")

    assert [
        dialog.roots_list.item(row).text() for row in range(dialog.roots_list.count())
    ] == [str(first_root), str(second_root)]
    dialog.close()


def test_settings_dialog_adds_root_and_saves_shared_config(app, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    config = AppConfig()
    destination = tmp_path / "config.toml"
    dialog = SettingsDialog(config, destination)

    assert dialog.add_root(Path("relative")) is False
    assert dialog.add_root(tmp_path / "missing") is False
    assert dialog.add_root(root) is True
    assert dialog.add_root(root) is False
    dialog.save()

    assert config.app.allowed_project_roots == [root.resolve()]
    assert root.as_posix() in destination.read_text(encoding="utf-8")
    assert str(destination) in dialog.status_label.text()


def test_settings_dialog_export_round_trips_allowed_roots(app, tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    config = AppConfig()
    destination = tmp_path / "config.toml"
    dialog = SettingsDialog(config, destination)
    assert dialog.add_root(root) is True

    dialog.save()
    reloaded = load_config(destination)

    assert reloaded.app.allowed_project_roots == config.app.allowed_project_roots


def test_settings_dialog_removes_root_from_config_and_file(app, tmp_path):
    removed_root = tmp_path / "remove"
    kept_root = tmp_path / "keep"
    removed_root.mkdir()
    kept_root.mkdir()
    config = AppConfig()
    config.app.allowed_project_roots[:] = [removed_root, kept_root]
    destination = tmp_path / "config.toml"
    dialog = SettingsDialog(config, destination)
    dialog.roots_list.setCurrentRow(0)

    dialog.remove_selected_root()
    dialog.save()

    assert config.app.allowed_project_roots == [kept_root]
    assert load_config(destination).app.allowed_project_roots == [kept_root]


def test_settings_dialog_shows_warning_for_empty_root_list(app, tmp_path):
    dialog = SettingsDialog(AppConfig(), tmp_path / "config.toml")
    dialog.show()
    app.processEvents()

    assert dialog.warning_label.isVisible()
    assert dialog.warning_label.text() == "허용 루트가 없으면 모든 작업 실행이 차단됩니다"
    dialog.close()


def test_settings_dialog_cancel_leaves_config_and_file_untouched(app, tmp_path):
    existing_root = tmp_path / "existing"
    added_root = tmp_path / "added"
    existing_root.mkdir()
    added_root.mkdir()
    config = AppConfig()
    config.app.allowed_project_roots[:] = [existing_root]
    destination = tmp_path / "config.toml"
    export_config(config, destination)
    original_file = destination.read_bytes()
    dialog = SettingsDialog(config, destination)
    assert dialog.add_root(added_root) is True

    dialog.cancel_button.click()

    assert config.app.allowed_project_roots == [existing_root]
    assert destination.read_bytes() == original_file


def test_settings_save_applies_new_root_to_existing_task_service(app, tmp_path):
    allowed_root = tmp_path / "workspace"
    project_path = allowed_root / "project"
    project_path.mkdir(parents=True)
    config = AppConfig()
    engine = create_db_engine(tmp_path / "queue-test.db")
    init_db(engine)
    service = TaskService(make_session_factory(engine), config)
    dialog = SettingsDialog(config, tmp_path / "config.toml")
    assert dialog.add_root(allowed_root) is True

    dialog.save()
    created = service.create(
        TaskDraft(
            title="라이브 설정 확인",
            prompt="새 허용 루트 아래에서 작업을 생성한다.",
            project_path=project_path,
        )
    )

    assert Path(created.project_path) == project_path.resolve()
    engine.dispose()


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


def test_log_viewer_toggles_between_pretty_and_raw_content(app, tmp_path):
    log_path = tmp_path / "claude.jsonl"
    raw_content = (
        '{"type":"assistant","message":{"content":'
        '[{"type":"text","text":"Readable result"}]}}\n'
    )
    log_path.write_text(raw_content, encoding="utf-8")
    viewer = LogViewer()
    viewer.stop_polling()

    viewer.set_run(
        SimpleNamespace(
            attempt=1,
            agent=AgentName.CLAUDE,
            exit_code=0,
            stdout_path=str(log_path),
            stderr_path=None,
        )
    )

    assert viewer.raw_checkbox.text() == "원본 보기"
    assert viewer.raw_checkbox.isChecked() is False
    assert viewer.stdout_view.toPlainText() == "Readable result\n"
    viewer.raw_checkbox.setChecked(True)
    assert viewer.stdout_view.toPlainText() == raw_content
    viewer.close()


def test_tray_notification_falls_back_when_system_tray_is_unavailable(app, monkeypatch):
    messages = []
    tray = QueueSystemTray(fallback=messages.append)
    monkeypatch.setattr(tray, "isSystemTrayAvailable", lambda: False)

    tray.notify("AI Queue Runner", "작업 완료")

    assert messages == ["AI Queue Runner: 작업 완료"]
