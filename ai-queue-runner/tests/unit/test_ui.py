"""Headless smoke tests for the native desktop UI."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QDate, Qt
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox

from ai_queue_runner.application.prompt_template_service import PromptTemplateService
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.exporter import export_config
from ai_queue_runner.config.loader import load_config
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import (
    AgentName,
    AgentPolicy,
    AgentStatus,
    ApprovalMode,
    ErrorType,
    TaskCreator,
    TaskEventAction,
    TaskStatus,
)
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.ui.approval_dialog import ApprovalDialog
from ai_queue_runner.ui.calendar_view import CalendarView
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
        "created_by": TaskCreator.USER,
        "creator_session_id": None,
        "agent_policy": AgentPolicy.CODEX,
        "assigned_agent": None,
        "fallback_agent": None,
        "priority": 70,
        "estimated_tokens": 0,
        "status": TaskStatus.WAITING,
        "approval_mode": ApprovalMode.CONFIRM,
        "auto_commit": False,
        "max_retries": 2,
        "retry_count": 0,
        "retry_delay_minutes": 10,
        "recurrence_interval_days": None,
        "recurrence_source_task_id": None,
        "not_before": None,
        "created_at": utcnow(),
        "finished_at": None,
        "tags": [],
        "dependency_ids": [],
        "last_error": None,
        "archived": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def make_config_with_roots(*roots: Path) -> AppConfig:
    config = AppConfig()
    config.app.allowed_project_roots[:] = roots
    return config


def test_task_table_model_exposes_compact_korean_projection(app):
    model = TaskTableModel([make_task()])

    assert model.rowCount() == 1
    assert model.columnCount() == 6
    assert model.data(model.index(0, TaskColumn.PRIORITY)) == 70
    assert model.data(model.index(0, TaskColumn.AGENT)) == "Codex"
    assert model.data(model.index(0, TaskColumn.STATUS)) == "대기"
    assert model.data(model.index(0, TaskColumn.PROJECT)) == "sample"
    assert model.data(model.index(0, TaskColumn.CREATOR)) == "user"
    assert model.data(model.index(0, 0), Qt.ItemDataRole.UserRole).id == 1


def test_retry_wait_row_shows_local_retry_time_and_error_tooltip(app):
    retry_at = (utcnow() + timedelta(minutes=12)).replace(second=0, microsecond=0)
    task = make_task(
        status=TaskStatus.RETRY_WAIT,
        not_before=retry_at,
        last_error="CLI를 찾을 수 없습니다",
    )
    model = TaskTableModel([task])
    status_index = model.index(0, TaskColumn.STATUS)

    expected_time = retry_at.replace(tzinfo=UTC).astimezone().strftime("%H:%M")
    assert model.data(status_index) == f"재시도 대기 ({expected_time})"
    assert "CLI를 찾을 수 없습니다" in model.data(
        status_index,
        Qt.ItemDataRole.ToolTipRole,
    )


def test_task_dialog_validates_and_builds_draft_with_dependencies(app, tmp_path):
    dependency = make_task(2, title="스키마 작성")
    project = tmp_path / "sample"
    project.mkdir()
    dialog = TaskDialog(
        available_tasks=[dependency],
        config=make_config_with_roots(project),
    )

    with pytest.raises(ValueError, match="필수 입력"):
        dialog.build_draft()

    dialog.title_edit.setText("UI 연결")
    dialog.prompt_edit.setPlainText("작업 목록을 연결한다.")
    dialog.estimated_tokens_spin.setValue(12_000)
    dialog.auto_commit_check.setChecked(True)
    dialog.dependencies_list.item(0).setCheckState(Qt.CheckState.Checked)
    draft = dialog.build_draft()

    assert draft.title == "UI 연결"
    assert draft.project_path == project.resolve()
    assert draft.agent_policy is AgentPolicy.ANY
    assert draft.approval_mode is ApprovalMode.CONFIRM
    assert draft.estimated_tokens == 12_000
    assert draft.auto_commit is True
    assert draft.dependency_ids == [2]


def test_task_dialog_preset_not_before_is_saved_as_naive_utc(app, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    local_zone = datetime.now().astimezone().tzinfo
    preset = datetime(2030, 4, 5, 9, tzinfo=local_zone)
    config = make_config_with_roots(project)
    dialog = TaskDialog(config=config, preset_not_before=preset)
    dialog.title_edit.setText("날짜 지정 작업")
    dialog.prompt_edit.setPlainText("선택한 날짜 오전 아홉 시부터 실행한다.")

    engine = create_db_engine(tmp_path / "calendar.db")
    init_db(engine)
    service = TaskService(make_session_factory(engine), config)
    created = service.create(dialog.build_draft())

    assert dialog.not_before_check.isChecked()
    shown = dialog.not_before_edit.dateTime().toPython()
    assert (shown.year, shown.month, shown.day, shown.hour, shown.minute) == (2030, 4, 5, 9, 0)
    assert created.not_before == preset.astimezone(UTC).replace(tzinfo=None)
    dialog.close()
    engine.dispose()


def test_task_dialog_sets_and_loads_daily_weekly_and_custom_recurrence(app, tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    config = make_config_with_roots(project)
    dialog = TaskDialog(config=config)
    dialog.title_edit.setText("반복 점검")
    dialog.prompt_edit.setPlainText("정기 점검을 실행한다.")

    assert [
        dialog.recurrence_combo.itemData(index)
        for index in range(dialog.recurrence_combo.count())
    ] == [None, 1, 7, "custom"]

    dialog.recurrence_combo.setCurrentIndex(dialog.recurrence_combo.findData(1))
    assert dialog.build_draft().recurrence_interval_days == 1
    dialog.recurrence_combo.setCurrentIndex(dialog.recurrence_combo.findData(7))
    assert dialog.build_draft().recurrence_interval_days == 7
    dialog.recurrence_combo.setCurrentIndex(
        dialog.recurrence_combo.findData("custom")
    )
    dialog.recurrence_days_spin.setValue(3)
    assert dialog.recurrence_days_spin.isEnabled()
    assert dialog.build_draft().recurrence_interval_days == 3

    edit_dialog = TaskDialog(
        make_task(project_path=str(project), recurrence_interval_days=7),
        config=config,
    )
    assert edit_dialog.recurrence_combo.currentData() == 7
    assert edit_dialog.build_draft().recurrence_interval_days == 7

    dialog.close()
    edit_dialog.close()


def test_task_dialog_lists_live_configured_roots_on_open(app, tmp_path):
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    config = make_config_with_roots(first_root)
    dialog = TaskDialog(config=config)
    config.app.allowed_project_roots[:] = [second_root, first_root]

    dialog.show()
    app.processEvents()

    assert [dialog.root_combo.itemText(index) for index in range(dialog.root_combo.count())] == [
        str(second_root),
        str(first_root),
    ]
    dialog.close()


def test_task_dialog_accepts_and_shows_subfolder_inside_allowed_root(
    app, tmp_path, monkeypatch
):
    root = tmp_path / "workspace"
    project = root / "project"
    project.mkdir(parents=True)
    dialog = TaskDialog(config=make_config_with_roots(root))
    browse_starts = []

    def choose_subfolder(*args):
        browse_starts.append(args[2])
        return str(project)

    monkeypatch.setattr(
        "ai_queue_runner.ui.task_dialog.QFileDialog.getExistingDirectory",
        choose_subfolder,
    )

    dialog.subfolder_button.click()

    assert browse_starts == [str(root.resolve())]
    assert dialog.path_display.text() == str(project.resolve())


def test_task_dialog_rejects_folder_outside_roots_and_keeps_previous_path(
    app, tmp_path, monkeypatch
):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    dialog = TaskDialog(config=make_config_with_roots(root))
    previous_path = dialog.path_display.text()
    warnings = []
    monkeypatch.setattr(
        "ai_queue_runner.ui.task_dialog.QFileDialog.getExistingDirectory",
        lambda *_args: str(outside),
    )
    monkeypatch.setattr(
        "ai_queue_runner.ui.task_dialog.QMessageBox.warning",
        lambda *_args: warnings.append(_args[2]),
    )

    dialog.subfolder_button.click()

    assert warnings == ["선택한 폴더는 허용된 프로젝트 루트 밖에 있습니다."]
    assert dialog.path_display.text() == previous_path


def test_task_dialog_empty_roots_disable_path_row_and_block_accept(app, monkeypatch):
    dialog = TaskDialog(config=AppConfig())
    dialog.title_edit.setText("차단 확인")
    dialog.prompt_edit.setPlainText("허용 루트가 없으면 저장하지 않는다.")
    warnings = []
    monkeypatch.setattr(
        "ai_queue_runner.ui.task_dialog.QMessageBox.warning",
        lambda *_args: warnings.append(_args[2]),
    )

    assert dialog.root_combo.isEnabled() is False
    assert dialog.subfolder_button.isEnabled() is False
    assert dialog.path_display.isEnabled() is False
    assert dialog.path_warning_label.text() == "설정에서 허용 프로젝트 루트를 먼저 추가하세요."
    with pytest.raises(ValueError, match="허용 프로젝트 루트"):
        dialog.build_draft()

    dialog.accept()

    assert dialog.result() == 0
    assert warnings == ["설정에서 허용 프로젝트 루트를 먼저 추가하세요."]


def test_task_dialog_edit_mode_selects_containing_root_and_existing_path(app, tmp_path):
    first_root = tmp_path / "first"
    containing_root = tmp_path / "workspace"
    project = containing_root / "project"
    first_root.mkdir()
    project.mkdir(parents=True)
    task = make_task(project_path=str(project), estimated_tokens=8_000)

    dialog = TaskDialog(
        task,
        config=make_config_with_roots(first_root, containing_root),
    )

    assert dialog.root_combo.currentText() == str(containing_root)
    assert dialog.path_display.text() == str(project.resolve())
    assert dialog.build_draft().estimated_tokens == 8_000


def test_task_dialog_root_switch_resets_effective_path(app, tmp_path):
    first_root = tmp_path / "first"
    project = first_root / "project"
    second_root = tmp_path / "second"
    project.mkdir(parents=True)
    second_root.mkdir()
    dialog = TaskDialog(
        make_task(project_path=str(project)),
        config=make_config_with_roots(first_root, second_root),
    )

    dialog.root_combo.setCurrentIndex(1)

    assert dialog.path_display.text() == str(second_root.resolve())


def test_task_dialog_stale_edit_path_requires_root_reselection(app, tmp_path):
    root = tmp_path / "workspace"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    dialog = TaskDialog(
        make_task(project_path=str(outside)),
        config=make_config_with_roots(root),
    )

    assert dialog.root_combo.currentIndex() == -1
    assert dialog.path_display.text() == str(outside)
    assert "현재 허용 루트 밖" in dialog.path_warning_label.text()
    with pytest.raises(ValueError, match="다시 선택"):
        dialog.build_draft()

    dialog.root_combo.setCurrentIndex(0)

    assert dialog.build_draft().project_path == root.resolve()


def test_task_dialog_saves_selects_and_deletes_prompt_templates(app, tmp_path, monkeypatch):
    engine = create_db_engine(tmp_path / "queue.db")
    init_db(engine)
    service = PromptTemplateService(make_session_factory(engine))
    service.save("Existing", "Original prompt")
    service.save("Other", "Other prompt")
    dialog = TaskDialog(
        config=make_config_with_roots(tmp_path),
        prompt_template_service=service,
    )

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


def test_approval_dialog_uses_worktree_execution_path(app):
    task = make_task(
        assigned_agent=AgentName.CODEX,
        worktree_path="C:/worktrees/T1",
        worktree_branch="aqr/T1",
    )
    adapter = SimpleNamespace(
        build_command=lambda value: ["codex", "--cd", value.project_path],
        build_stdin=lambda value: f"work inside {value.project_path}",
    )

    dialog = ApprovalDialog(task, adapter)

    assert dialog.path_value.text() == task.worktree_path
    assert task.worktree_path in dialog.command_text
    assert task.worktree_path in dialog.prompt_text


class FakeTaskService:
    def __init__(self, tasks):
        self.tasks = tasks
        self.summary_dates = []

    def list(self, status=None):
        if status is None:
            return self.tasks
        return [task for task in self.tasks if task.status is status]

    def get(self, task_id):
        return next((task for task in self.tasks if task.id == task_id), None)

    def activity_between(self, start_utc, end_utc):
        return [
            task
            for task in self.tasks
            if any(
                value is not None and start_utc <= value < end_utc
                for value in (
                    getattr(task, "created_at", None),
                    getattr(task, "finished_at", None),
                    getattr(task, "not_before", None),
                )
            )
        ]

    def daily_summary(self, selected_date):
        self.summary_dates.append(selected_date)
        totals = SimpleNamespace(
            succeeded_count=0,
            failed_count=0,
            blocked_count=0,
            cancelled_count=0,
            total_runtime_seconds=0,
            claude_run_count=0,
            codex_run_count=0,
            fallback_count=0,
            claude_usage_limit_wait_occurred=False,
            codex_usage_limit_wait_occurred=False,
        )
        return SimpleNamespace(local_date=selected_date, totals=totals, projects=())

    def list_runs(self, task_id):
        return []

    def delete(self, task_id):
        self.tasks = [task for task in self.tasks if task.id != task_id]

    def cancel(self, task_id):
        task = self.get(task_id)
        task.status = TaskStatus.CANCELLED
        return task


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
        self.approved = []
        self.rejected = []
        self.cancelled = []
        self.shutdown_calls = 0

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

    def approve(self, task_id):
        self.approved.append(task_id)
        return True

    def reject(self, task_id, *, block=False):
        self.rejected.append((task_id, block))
        return True

    def cancel_running(self, task_id):
        self.cancelled.append(task_id)
        return True

    def shutdown(self):
        self.shutdown_calls += 1


def test_calendar_marks_and_groups_activity_for_selected_local_date(app):
    local_zone = datetime.now().astimezone().tzinfo
    selected = datetime.now().astimezone().date()

    def utc_at(hour):
        return datetime(
            selected.year,
            selected.month,
            selected.day,
            hour,
            tzinfo=local_zone,
        ).astimezone(UTC).replace(tzinfo=None)

    service = FakeTaskService(
        [
            make_task(
                1,
                title="완료된 작업",
                status=TaskStatus.SUCCEEDED,
                created_at=utc_at(1),
                finished_at=utc_at(10),
            ),
            make_task(
                2,
                title="취소된 작업",
                status=TaskStatus.CANCELLED,
                created_at=utc_at(2),
                finished_at=utc_at(11),
            ),
            make_task(3, title="예정 작업", created_at=utc_at(3), not_before=utc_at(9)),
            make_task(4, title="생성된 작업", created_at=utc_at(4)),
        ]
    )
    view = CalendarView(service)
    view.calendar.setSelectedDate(QDate(selected.year, selected.month, selected.day))
    view.refresh()

    grouped = {
        view.task_tree.topLevelItem(index).text(0).split(" (")[0]: [
            view.task_tree.topLevelItem(index).child(row).text(0)
            for row in range(view.task_tree.topLevelItem(index).childCount())
        ]
        for index in range(view.task_tree.topLevelItemCount())
    }

    assert selected in view.marked_dates
    assert any("완료된 작업" in text for text in grouped["완료"])
    assert any("취소된 작업" in text for text in grouped["실패·취소"])
    assert any("예정 작업" in text for text in grouped["예정"])
    assert any("생성된 작업" in text for text in grouped["생성"])
    view.close()


def test_calendar_summary_button_opens_selected_date_summary(app, monkeypatch):
    service = FakeTaskService([])
    selected = datetime.now().astimezone().date()
    shown = []
    monkeypatch.setattr(
        "ai_queue_runner.ui.calendar_view.QMessageBox.information",
        lambda _parent, title, text: shown.append((title, text)),
    )
    view = CalendarView(service)
    view.calendar.setSelectedDate(QDate(selected.year, selected.month, selected.day))

    view.summary_button.click()

    assert service.summary_dates == [selected]
    assert shown[0][0] == f"{selected.isoformat()} 일일 요약"
    assert "프로젝트 활동이 없습니다." in shown[0][1]
    view.close()


def test_calendar_add_flow_prefills_local_nine_and_focuses_main_table(
    app, tmp_path, monkeypatch
):
    project = tmp_path / "project"
    project.mkdir()
    task = make_task(project_path=str(project))
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=make_config_with_roots(project),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    real_task_dialog = TaskDialog
    opened = []

    def make_dialog(*args, **kwargs):
        dialog = real_task_dialog(*args, **kwargs)
        dialog.exec = lambda: QDialog.DialogCode.Rejected
        opened.append(dialog)
        return dialog

    monkeypatch.setattr("ai_queue_runner.ui.main_window.TaskDialog", make_dialog)
    selected = datetime.now().astimezone().date()
    window.calendar_view.calendar.setSelectedDate(
        QDate(selected.year, selected.month, selected.day)
    )

    window.calendar_view.add_button.click()

    assert len(opened) == 1
    shown = opened[0].not_before_edit.dateTime().toPython()
    assert opened[0].not_before_check.isChecked()
    assert (shown.year, shown.month, shown.day, shown.hour, shown.minute) == (
        selected.year,
        selected.month,
        selected.day,
        9,
        0,
    )

    window.calendar_view.focus_task_requested.emit(task.id)
    app.processEvents()
    assert window.tabs.currentWidget() is window.queue_page
    assert window.task_table.selectionModel().selectedRows()[0].row() == 0
    opened[0].close()
    window.close()


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


def test_main_window_filters_projects_and_shows_creator_event_history(app):
    first = make_task(
        1,
        project_path="C:/work/first",
        created_by=TaskCreator.CODEX,
        creator_session_id="session-9",
    )
    second = make_task(2, project_path="C:/work/second")
    service = FakeTaskService([first, second])
    service.list_events = lambda _task_id, limit=10: [
        SimpleNamespace(
            created_at=utcnow(),
            actor=TaskCreator.CODEX,
            action=TaskEventAction.CREATED,
        )
    ][:limit]
    window = MainWindow(
        service,
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()

    window.project_filter.setCurrentIndex(
        window.project_filter.findData(first.project_path)
    )
    app.processEvents()
    assert [task.id for task in window.task_model.tasks] == [first.id]

    window.task_table.selectRow(0)
    app.processEvents()
    details = window.details_view.toPlainText()
    assert "생성자: codex" in details
    assert "생성 세션: session-9" in details
    assert "최근 관리 이력:" in details
    assert "codex created" in details
    window.close()


def test_main_window_details_show_fallback_history(app):
    task = make_task(
        agent_policy=AgentPolicy.ANY,
        fallback_agent=AgentName.CODEX,
    )
    service = FakeTaskService([task])
    service.list_runs = lambda _task_id: [
        SimpleNamespace(
            attempt=1,
            agent=AgentName.CLAUDE,
            exit_code=1,
            error_type=ErrorType.UNKNOWN,
            fallback_to_agent=AgentName.CODEX,
            stdout_path=None,
            stderr_path=None,
        )
    ]
    window = MainWindow(
        service,
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()

    window.task_table.selectRow(0)
    app.processEvents()

    details = window.details_view.toPlainText()
    assert "Fallback 우선 AI: codex" in details
    assert "claude → codex (unknown)" in details
    window.close()


def test_main_window_details_show_auto_commit_history(app):
    task = make_task(auto_commit=True)
    service = FakeTaskService([task])
    service.list_runs = lambda _task_id: [
        SimpleNamespace(
            attempt=1,
            agent=AgentName.CODEX,
            exit_code=0,
            error_type=None,
            fallback_to_agent=None,
            auto_commit_hash="abc123",
            auto_commit_result="committed",
            stdout_path=None,
            stderr_path=None,
        )
    ]
    window = MainWindow(
        service,
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()

    window.task_table.selectRow(0)
    app.processEvents()

    details = window.details_view.toPlainText()
    assert "자동 커밋 요청: 예" in details
    assert "자동 커밋 이력:" in details
    assert "abc123" in details
    window.close()


def test_delete_button_is_disabled_for_running_and_enabled_for_succeeded(app):
    running = make_task(1, status=TaskStatus.RUNNING)
    succeeded = make_task(2, status=TaskStatus.SUCCEEDED)
    window = MainWindow(
        FakeTaskService([running, succeeded]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()

    window.task_table.selectRow(0)
    app.processEvents()
    assert window.delete_button.isEnabled() is False

    window.task_table.selectRow(1)
    app.processEvents()
    assert window.delete_button.isEnabled() is True
    assert window.task_table.contextMenuPolicy() is Qt.ContextMenuPolicy.CustomContextMenu
    window.close()


def test_exit_without_running_tasks_quits_without_dialog(app, monkeypatch):
    runtime = FakeRuntime()
    window = MainWindow(
        FakeTaskService([make_task()]),
        FakeAgentService(),
        runtime,
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    monkeypatch.setattr(
        "ai_queue_runner.ui.main_window.QMessageBox.question",
        lambda *_args: pytest.fail("no exit dialog expected without running tasks"),
    )
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", lambda _self: quit_calls.append(True))

    window._quit_application()

    assert runtime.cancelled == []
    assert runtime.shutdown_calls == 1
    assert quit_calls == [True]
    window.close()


def test_exit_with_running_task_confirms_cancels_and_quits(app, monkeypatch):
    task = make_task(status=TaskStatus.RUNNING)
    runtime = FakeRuntime()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        runtime,
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    questions = []

    def confirm(*args):
        questions.append(args[2])
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr("ai_queue_runner.ui.main_window.QMessageBox.question", confirm)
    quit_calls = []
    monkeypatch.setattr(QApplication, "quit", lambda _self: quit_calls.append(True))

    window._quit_application()

    assert questions == ["정말 종료하시겠습니까? 진행 중인 작업은 모두 중단됩니다."]
    assert runtime.cancelled == [task.id]
    assert runtime.shutdown_calls == 1
    assert quit_calls == [True]
    window.close()


def test_enabled_agent_with_missing_cli_shows_warning(app):
    missing_claude = SimpleNamespace(name=AgentName.CLAUDE, is_installed=lambda: False)
    window = MainWindow(
        FakeTaskService([]),
        FakeAgentService(),
        FakeRuntime(),
        adapters={AgentName.CLAUDE: missing_claude},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    widget = window.agent_widgets[AgentName.CLAUDE]

    assert "claude CLI를 찾을 수 없습니다" in widget.cli_warning_label.text()
    assert widget.apply_button.isEnabled() is False
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


def test_run_now_confirm_task_is_not_reprompted_by_poll(app):
    task = make_task()
    runtime = FakeRuntime()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        runtime,
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    window.task_table.selectRow(0)
    app.processEvents()
    prompted = []
    window.request_approval = lambda task_id: prompted.append(task_id) or True

    window.run_now_task()
    task.status = TaskStatus.RESERVED
    window._poll_runtime()
    window._poll_runtime()

    assert runtime.reserved_now == [task.id]
    assert prompted == [task.id]
    window.close()


def test_run_now_auto_task_executes_without_approval_dialog(app):
    task = make_task(approval_mode=ApprovalMode.AUTO)
    runtime = FakeRuntime()
    window = MainWindow(
        FakeTaskService([task]),
        FakeAgentService(),
        runtime,
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    window.task_table.selectRow(0)
    app.processEvents()
    window.request_approval = lambda task_id: pytest.fail("auto 작업에는 승인 창이 없어야 합니다")

    window.run_now_task()

    assert runtime.reserved_now == [task.id]
    assert runtime.approved == [task.id]
    window.close()


def test_poll_does_not_stack_prompt_while_approval_dialog_open(app):
    task = make_task(
        status=TaskStatus.RESERVED,
        approval_mode=ApprovalMode.CONFIRM,
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
    window._approval_dialog_open = True

    window._poll_runtime()

    assert prompted == []
    window.close()


def test_cancel_reserved_task_rejects_reservation_then_cancels(app, monkeypatch):
    task = make_task(status=TaskStatus.RESERVED, assigned_agent=AgentName.CODEX)
    runtime = FakeRuntime()
    service = FakeTaskService([task])
    window = MainWindow(
        service,
        FakeAgentService(),
        runtime,
        adapters={},
        config=AppConfig(),
        active_config_path=Path("config.toml"),
        create_tray=False,
    )
    window.refresh_timer.stop()
    window.task_table.selectRow(0)
    app.processEvents()
    monkeypatch.setattr(
        "ai_queue_runner.ui.main_window.QMessageBox.question",
        lambda *_args: QMessageBox.StandardButton.Yes,
    )

    window.cancel_task()

    assert runtime.rejected == [(task.id, False)]
    assert task.status is TaskStatus.CANCELLED
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
