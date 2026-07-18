"""Primary queue-management window."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QItemSelection, Qt, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import AgentName, AgentStatus, ApprovalMode, TaskStatus
from ai_queue_runner.ui.agent_status_widget import AgentStatusWidget
from ai_queue_runner.ui.approval_dialog import ApprovalDialog
from ai_queue_runner.ui.calendar_view import CalendarView
from ai_queue_runner.ui.log_viewer import LogViewer
from ai_queue_runner.ui.settings_dialog import SettingsDialog
from ai_queue_runner.ui.task_dialog import TaskDialog
from ai_queue_runner.ui.task_table import TaskColumn, TaskTableModel, enum_value
from ai_queue_runner.ui.tray import QueueSystemTray


class MainWindow(QMainWindow):
    """Coordinate UI actions through application services only."""

    def __init__(
        self,
        task_service: object,
        agent_service: object,
        runtime: object,
        adapters: Mapping[object, object],
        parent: QWidget | None = None,
        *,
        config: AppConfig,
        active_config_path: Path,
        prompt_template_service: object | None = None,
        create_tray: bool = True,
    ) -> None:
        super().__init__(parent)
        self.task_service = task_service
        self.agent_service = agent_service
        self.runtime = runtime
        self.adapters = adapters
        self.config = config
        self.active_config_path = active_config_path
        self.prompt_template_service = prompt_template_service
        self._selected_task: object | None = None
        self._prompted_reservations: set[int] = set()
        self._approval_dialog_open = False

        self.setWindowTitle("AI Queue Runner")
        self.resize(1100, 760)
        self.setMinimumSize(960, 640)

        self.agent_widgets = {
            agent: AgentStatusWidget(agent) for agent in (AgentName.CLAUDE, AgentName.CODEX)
        }
        for widget in self.agent_widgets.values():
            widget.availability_changed.connect(self._set_agent_availability)

        self.start_button = QPushButton("스케줄러 시작")
        self.pause_button = QPushButton("일시정지")
        self.settings_button = QPushButton("설정")
        self.add_button = QPushButton("작업 추가")
        self.edit_button = QPushButton("수정")
        self.duplicate_button = QPushButton("복제")
        self.run_now_button = QPushButton("즉시 실행")
        self.move_top_button = QPushButton("맨 위로")
        self.retry_button = QPushButton("재시도")
        self.archive_button = QPushButton("완료 보관")
        self.cancel_button = QPushButton("취소")
        self.delete_button = QPushButton("삭제")
        self.stop_button = QPushButton("실행 중단")
        self.open_log_button = QPushButton("로그 열기")
        self.open_folder_button = QPushButton("프로젝트 폴더")
        self.refresh_button = QPushButton("새로고침")
        self.exit_button = QPushButton("종료")

        self.start_button.clicked.connect(self.start_scheduler)
        self.pause_button.clicked.connect(self.pause_scheduler)
        self.settings_button.clicked.connect(self.open_settings)
        self.add_button.clicked.connect(self.add_task)
        self.edit_button.clicked.connect(self.edit_task)
        self.duplicate_button.clicked.connect(self.duplicate_task)
        self.run_now_button.clicked.connect(self.run_now_task)
        self.move_top_button.clicked.connect(self.move_task_to_top)
        self.retry_button.clicked.connect(self.retry_task)
        self.archive_button.clicked.connect(self.archive_task)
        self.cancel_button.clicked.connect(self.cancel_task)
        self.delete_button.clicked.connect(self.delete_task)
        self.stop_button.clicked.connect(self.cancel_running_task)
        self.open_log_button.clicked.connect(self.open_log)
        self.open_folder_button.clicked.connect(self.open_project_folder)
        self.refresh_button.clicked.connect(self.refresh)
        self.exit_button.clicked.connect(self._quit_application)

        self.status_filter = QComboBox()
        self.status_filter.setAccessibleName("작업 상태 필터")
        for label, status in (
            ("전체", None),
            ("대기", TaskStatus.WAITING),
            ("예약", TaskStatus.RESERVED),
            ("실행", TaskStatus.RUNNING),
            ("재시도 대기", TaskStatus.RETRY_WAIT),
            ("차단", TaskStatus.BLOCKED),
            ("실패", TaskStatus.FAILED),
            ("완료", TaskStatus.SUCCEEDED),
            ("취소", TaskStatus.CANCELLED),
            ("보관됨", "archived"),
        ):
            self.status_filter.addItem(label, status)
        self.status_filter.currentIndexChanged.connect(self.refresh)
        self.project_filter = QComboBox()
        self.project_filter.setAccessibleName("프로젝트 필터")
        self.project_filter.addItem("모든 프로젝트", None)
        self.project_filter.currentIndexChanged.connect(self.refresh)

        self.task_model = TaskTableModel()
        self.task_table = QTableView()
        self.task_table.setModel(self.task_model)
        self.task_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.task_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.task_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.task_table.setAlternatingRowColors(True)
        self.task_table.verticalHeader().setVisible(False)
        self.task_table.verticalHeader().setDefaultSectionSize(28)
        header = self.task_table.horizontalHeader()
        header.setStretchLastSection(True)
        header.resizeSection(TaskColumn.PRIORITY, 72)
        header.resizeSection(TaskColumn.AGENT, 76)
        header.resizeSection(TaskColumn.STATUS, 140)
        header.resizeSection(TaskColumn.TITLE, 330)
        header.resizeSection(TaskColumn.CREATOR, 80)
        self.task_table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.task_table.doubleClicked.connect(lambda _index: self.edit_task())
        self.task_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.task_table.customContextMenuRequested.connect(self._show_task_context_menu)

        self.details_view = QPlainTextEdit()
        self.details_view.setReadOnly(True)
        self.details_view.setPlaceholderText("작업을 선택하면 상세 정보가 표시됩니다.")
        self.log_viewer = LogViewer()
        detail_splitter = QSplitter(Qt.Orientation.Horizontal)
        detail_splitter.addWidget(self.details_view)
        detail_splitter.addWidget(self.log_viewer)
        detail_splitter.setSizes([430, 650])

        content_splitter = QSplitter(Qt.Orientation.Vertical)
        content_splitter.addWidget(self.task_table)
        content_splitter.addWidget(detail_splitter)
        content_splitter.setSizes([390, 290])

        agent_row = QHBoxLayout()
        for widget in self.agent_widgets.values():
            agent_row.addWidget(widget)
        task_controls = QHBoxLayout()
        for widget in (
            self.add_button,
            self.edit_button,
            self.duplicate_button,
            self.run_now_button,
            self.move_top_button,
            self.retry_button,
            self.archive_button,
            self.cancel_button,
            self.delete_button,
            self.stop_button,
            self.open_log_button,
            self.open_folder_button,
        ):
            task_controls.addWidget(widget)
        task_controls.addStretch(1)
        scheduler_controls = QHBoxLayout()
        for widget in (
            self.start_button,
            self.pause_button,
            self.settings_button,
            self.exit_button,
        ):
            scheduler_controls.addWidget(widget)
        scheduler_controls.addStretch(1)
        scheduler_controls.addWidget(QLabel("필터"))
        scheduler_controls.addWidget(self.status_filter)
        scheduler_controls.addWidget(QLabel("프로젝트"))
        scheduler_controls.addWidget(self.project_filter)
        scheduler_controls.addWidget(self.refresh_button)

        self.queue_page = QWidget()
        queue_layout = QVBoxLayout(self.queue_page)
        queue_layout.setContentsMargins(8, 8, 8, 8)
        queue_layout.setSpacing(8)
        queue_layout.addLayout(task_controls)
        queue_layout.addLayout(scheduler_controls)
        queue_layout.addWidget(content_splitter, 1)

        self.calendar_view = CalendarView(self.task_service)
        self.calendar_view.focus_task_requested.connect(self._focus_task_from_calendar)
        self.calendar_view.add_task_requested.connect(self._add_task_on_date)
        self.tabs = QTabWidget()
        self.tabs.addTab(self.queue_page, "작업 목록")
        self.tabs.addTab(self.calendar_view, "달력")

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)
        layout.addLayout(agent_row)
        layout.addWidget(self.tabs, 1)
        self.setCentralWidget(central)
        self.statusBar().showMessage("준비")

        self.tray: QueueSystemTray | None = None
        if create_tray:
            self.tray = QueueSystemTray(self, fallback=self.statusBar().showMessage)
            self.tray.show_requested.connect(self.show_from_tray)
            self.tray.start_requested.connect(self.start_scheduler)
            self.tray.pause_requested.connect(self.pause_scheduler)
            self.tray.availability_requested.connect(self._set_tray_availability)
            self.tray.quit_requested.connect(self._quit_application)
            self.tray.show()

        self.refresh()
        self._last_task_statuses = {
            int(task.id): enum_value(getattr(task, "status", ""))
            for task in self._list_tasks()
        }
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(1000)
        self.refresh_timer.timeout.connect(self._poll_runtime)
        self.refresh_timer.start()

    def _list_tasks(self, status: TaskStatus | str | None = None) -> list[object]:
        return list(self.task_service.list(status=status))

    def _get_task(self, task_id: int) -> object | None:
        return self.task_service.get(task_id)

    def _list_agent_states(self) -> list[object]:
        return list(self.agent_service.list())

    def refresh(self, *_args: object) -> None:
        selected_id = getattr(self._selected_task, "id", None)
        try:
            tasks = self._list_tasks(self.status_filter.currentData())
            all_tasks = self._list_tasks()
            archived_tasks = self._list_tasks("archived")
            states = self._list_agent_states()
        except Exception as exc:  # UI boundary: service failures remain visible.
            self.statusBar().showMessage(f"새로고침 실패: {exc}")
            return
        self._sync_project_filter([*all_tasks, *archived_tasks, *tasks])
        selected_project = self.project_filter.currentData()
        if selected_project is not None:
            tasks = [
                task
                for task in tasks
                if str(getattr(task, "project_path", "")) == selected_project
            ]
        self.task_model.set_tasks(tasks)
        by_agent = {enum_value(getattr(state, "agent", "")): state for state in states}
        for agent, widget in self.agent_widgets.items():
            widget.set_state(by_agent.get(agent.value))
            settings = self.config.agents.get(agent.value)
            adapter = self._adapter_for_agent(agent)
            try:
                resolvable = bool(adapter is not None and adapter.is_installed())
            except Exception:
                resolvable = False
            widget.set_cli_resolvable(
                enabled=bool(settings is not None and settings.enabled),
                resolvable=resolvable,
            )
        self._restore_selection(selected_id)
        self.calendar_view.refresh()
        running = bool(getattr(self.runtime, "running", False))
        paused = bool(getattr(self.runtime, "paused", not running))
        self.start_button.setEnabled(not running or paused)
        self.pause_button.setEnabled(running and not paused)
        if self.tray is not None:
            self.tray.set_running(running and not paused)
            waiting = next(
                (
                    task
                    for task in self.task_model.tasks
                    if enum_value(getattr(task, "status", "")) == TaskStatus.WAITING.value
                ),
                None,
            )
            self.tray.set_next_task(waiting)
        self._update_action_state()
        self.statusBar().showMessage(f"작업 {len(tasks)}개", 3000)

    def _sync_project_filter(self, tasks: list[object]) -> None:
        selected = self.project_filter.currentData()
        paths = sorted(
            {str(getattr(task, "project_path", "")) for task in tasks}
            - {""},
            key=str.casefold,
        )
        self.project_filter.blockSignals(True)
        self.project_filter.clear()
        self.project_filter.addItem("모든 프로젝트", None)
        for path in paths:
            name = Path(path).name or path
            self.project_filter.addItem(f"{name} — {path}", path)
        index = self.project_filter.findData(selected)
        self.project_filter.setCurrentIndex(max(index, 0))
        self.project_filter.blockSignals(False)

    def _restore_selection(self, task_id: object) -> None:
        if task_id is None:
            self._clear_selection_details()
            return
        for row, task in enumerate(self.task_model.tasks):
            if getattr(task, "id", None) == task_id:
                self.task_table.selectRow(row)
                return
        self._clear_selection_details()

    def _selection_changed(
        self,
        selected: QItemSelection,
        _deselected: QItemSelection,
    ) -> None:
        indexes = selected.indexes() or self.task_table.selectionModel().selectedRows()
        if not indexes:
            self._clear_selection_details()
            return
        task = self.task_model.task_at(indexes[0].row())
        task_id = getattr(task, "id", None)
        try:
            self._selected_task = self._get_task(task_id) if task_id is not None else task
        except Exception as exc:
            self.statusBar().showMessage(f"작업 상세 조회 실패: {exc}")
            self._selected_task = task
        self._show_selected_details()

    def _clear_selection_details(self) -> None:
        self._selected_task = None
        self.details_view.clear()
        self.log_viewer.clear()
        self._update_action_state()

    def _show_selected_details(self) -> None:
        task = self._selected_task
        if task is None:
            self._clear_selection_details()
            return
        assigned = getattr(task, "assigned_agent", None) or getattr(task, "agent_policy", "")
        details = [
            f"#{getattr(task, 'id', '')}  {getattr(task, 'title', '')}",
            f"상태: {enum_value(getattr(task, 'status', ''))}",
            f"담당 AI: {enum_value(assigned)}",
            f"우선순위: {getattr(task, 'priority', '')}",
            f"예상 토큰: {getattr(task, 'estimated_tokens', 0):,}",
            f"프로젝트: {getattr(task, 'project_path', '')}",
            f"생성자: {enum_value(getattr(task, 'created_by', 'unknown'))}",
            "생성 세션: " + str(getattr(task, "creator_session_id", None) or "-"),
            f"재시도: {getattr(task, 'retry_count', 0)} / {getattr(task, 'max_retries', 0)}",
            "자동 커밋 요청: "
            + ("예" if bool(getattr(task, "auto_commit", False)) else "아니요"),
            "Draft PR 요청: "
            + ("예" if bool(getattr(task, "draft_pr", False)) else "아니요"),
        ]
        review_verdict = enum_value(getattr(task, "review_verdict", None))
        review_score = getattr(task, "review_score", None)
        review_text = review_verdict or "미평가"
        if review_score is not None:
            review_text += f" (점수 {review_score}/5)"
        details.append(f"품질 평가: {review_text}")
        worktree_path = getattr(task, "worktree_path", None)
        worktree_branch = getattr(task, "worktree_branch", None)
        if worktree_path and worktree_branch:
            details.extend(
                (
                    f"Worktree 브랜치: {worktree_branch}",
                    f"Worktree 경로: {worktree_path}",
                )
            )
        fallback_agent = getattr(task, "fallback_agent", None)
        if fallback_agent is not None:
            details.append(f"Fallback 우선 AI: {enum_value(fallback_agent)}")
        last_error = getattr(task, "last_error", None)
        if last_error:
            details.extend(("", "마지막 오류:", str(last_error)))
        try:
            runs = list(self.task_service.list_runs(task.id))
        except Exception as exc:
            self.statusBar().showMessage(f"실행 기록 조회 실패: {exc}")
            runs = []
        fallback_runs = [run for run in runs if getattr(run, "fallback_to_agent", None)]
        if fallback_runs:
            details.extend(("", "Fallback 이력:"))
            details.extend(
                f"- attempt {run.attempt}: {enum_value(run.agent)} → "
                f"{enum_value(run.fallback_to_agent)} ({enum_value(run.error_type)})"
                for run in fallback_runs[-5:]
            )
        auto_commit_runs = [
            run
            for run in runs
            if getattr(run, "auto_commit_hash", None)
            or getattr(run, "auto_commit_result", None)
        ]
        if auto_commit_runs:
            details.extend(("", "자동 커밋 이력:"))
            details.extend(
                f"- attempt {run.attempt}: "
                f"{getattr(run, 'auto_commit_hash', None) or run.auto_commit_result}"
                for run in auto_commit_runs[-5:]
            )
        draft_pr_runs = [
            run
            for run in runs
            if getattr(run, "draft_pr_url", None)
            or getattr(run, "draft_pr_result", None)
        ]
        if draft_pr_runs:
            details.extend(("", "Draft PR 이력:"))
            details.extend(
                f"- attempt {run.attempt}: "
                f"{getattr(run, 'draft_pr_url', None) or run.draft_pr_result}"
                for run in draft_pr_runs[-5:]
            )
        list_events = getattr(self.task_service, "list_events", None)
        if callable(list_events):
            try:
                events = list(list_events(task.id, limit=10))
            except Exception as exc:
                self.statusBar().showMessage(f"관리 이력 조회 실패: {exc}")
                events = []
            if events:
                details.extend(("", "최근 관리 이력:"))
                details.extend(
                    f"- {getattr(event, 'created_at', '')} "
                    f"{enum_value(getattr(event, 'actor', ''))} "
                    f"{enum_value(getattr(event, 'action', ''))}"
                    for event in events
                )
        details.extend(("", "프롬프트:", str(getattr(task, "prompt", ""))))
        self.details_view.setPlainText("\n".join(details))
        self.log_viewer.set_run(runs[-1] if runs else None)
        self._update_action_state()

    def _update_action_state(self) -> None:
        has_task = self._selected_task is not None
        status = enum_value(getattr(self._selected_task, "status", ""))
        self.edit_button.setEnabled(has_task and status == TaskStatus.WAITING.value)
        self.duplicate_button.setEnabled(has_task)
        self.retry_button.setEnabled(
            has_task and status in {TaskStatus.FAILED.value, TaskStatus.BLOCKED.value}
        )
        self.run_now_button.setEnabled(has_task and status == TaskStatus.WAITING.value)
        self.move_top_button.setEnabled(has_task and status == TaskStatus.WAITING.value)
        self.archive_button.setEnabled(
            has_task
            and status == TaskStatus.SUCCEEDED.value
            and not bool(getattr(self._selected_task, "archived", False))
        )
        self.open_log_button.setEnabled(has_task)
        self.open_folder_button.setEnabled(has_task)
        self.cancel_button.setEnabled(
            has_task
            and status
            in {
                TaskStatus.WAITING.value,
                TaskStatus.RESERVED.value,
                TaskStatus.RETRY_WAIT.value,
                TaskStatus.BLOCKED.value,
            }
        )
        self.delete_button.setEnabled(
            has_task
            and status not in {TaskStatus.RESERVED.value, TaskStatus.RUNNING.value}
        )
        running = has_task and status == TaskStatus.RUNNING.value
        if running and hasattr(self.runtime, "is_active"):
            try:
                running = bool(self.runtime.is_active(getattr(self._selected_task, "id", None)))
            except TypeError:
                running = bool(self.runtime.is_active())
        self.stop_button.setEnabled(running)

    def _poll_runtime(self) -> None:
        """Refresh visible state and surface each confirmation reservation once."""
        self.refresh()
        try:
            tasks = self._list_tasks()
        except Exception:
            return
        self._notify_task_transitions(tasks)
        # AIDEV-NOTE: dialog.exec() spins a nested event loop, so this poll re-enters
        # while an approval dialog is open; without this guard a second dialog stacks.
        if self._approval_dialog_open:
            return
        reserved = {
            int(task.id): task
            for task in tasks
            if enum_value(getattr(task, "status", "")) == TaskStatus.RESERVED.value
        }
        self._prompted_reservations.intersection_update(reserved)
        for task_id in reserved:
            if task_id in self._prompted_reservations:
                continue
            try:
                active = bool(self.runtime.is_active(task_id))
            except (AttributeError, TypeError):
                active = False
            if active:
                continue
            self._prompted_reservations.add(task_id)
            self.request_approval(task_id)
            break

    def _notify(self, message: str) -> None:
        self.statusBar().showMessage(message, 5000)
        if self.tray is not None:
            self.tray.notify("AI Queue Runner", message)

    def _notify_task_transitions(self, tasks: list[object]) -> None:
        current = {
            int(task.id): enum_value(getattr(task, "status", ""))
            for task in tasks
        }
        messages = {
            TaskStatus.RUNNING.value: "실행을 시작했습니다.",
            TaskStatus.SUCCEEDED.value: "실행을 완료했습니다.",
            TaskStatus.FAILED.value: "실행에 실패했습니다.",
            TaskStatus.BLOCKED.value: "사용자 확인이 필요합니다.",
        }
        for task in tasks:
            task_id = int(task.id)
            status = current[task_id]
            if self._last_task_statuses.get(task_id) == status or status not in messages:
                continue
            message = f"#{task_id} {getattr(task, 'title', '')}: {messages[status]}"
            if self.tray is not None:
                self.tray.notify("AI Queue Runner", message)
            self.statusBar().showMessage(message, 5000)
        self._last_task_statuses = current

    def _show_error(self, action: str, exc: Exception) -> None:
        self.statusBar().showMessage(f"{action} 실패: {exc}")
        QMessageBox.critical(self, f"{action} 실패", str(exc))

    def start_scheduler(self) -> None:
        try:
            self.runtime.start()
        except Exception as exc:
            self._show_error("스케줄러 시작", exc)
            return
        self._notify("스케줄러를 시작했습니다.")
        self.refresh()

    def pause_scheduler(self) -> None:
        try:
            self.runtime.pause()
        except Exception as exc:
            self._show_error("일시정지", exc)
            return
        self._notify("스케줄러를 일시정지했습니다.")
        self.refresh()

    def open_settings(self) -> None:
        dialog = SettingsDialog(self.config, self.active_config_path, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.statusBar().showMessage(f"설정을 저장했습니다: {self.active_config_path}", 5000)

    def add_task(self) -> None:
        self._open_add_task_dialog()

    def _add_task_on_date(self, preset_not_before: datetime) -> None:
        self._open_add_task_dialog(preset_not_before)

    def _open_add_task_dialog(
        self,
        preset_not_before: datetime | None = None,
    ) -> None:
        dialog = TaskDialog(
            available_tasks=self._list_tasks(),
            parent=self,
            config=self.config,
            prompt_template_service=self.prompt_template_service,
            preset_not_before=preset_not_before,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.task_service.create(dialog.build_draft())
        except Exception as exc:
            self._show_error("작업 추가", exc)
            return
        self._notify("작업을 추가했습니다.")
        self.refresh()

    def _focus_task_from_calendar(self, task_id: int) -> None:
        try:
            task = self._get_task(task_id)
        except Exception as exc:
            self._show_error("작업 찾기", exc)
            return
        if task is None:
            self.statusBar().showMessage(f"작업 #{task_id}을 찾을 수 없습니다.")
            return
        self.status_filter.setCurrentIndex(9 if getattr(task, "archived", False) else 0)
        self.refresh()
        for row, listed_task in enumerate(self.task_model.tasks):
            if getattr(listed_task, "id", None) == task_id:
                self.task_table.selectRow(row)
                self.task_table.scrollTo(self.task_model.index(row, 0))
                self.tabs.setCurrentWidget(self.queue_page)
                return
        self.statusBar().showMessage(f"작업 #{task_id}을 현재 목록에서 찾을 수 없습니다.")

    def edit_task(self) -> None:
        if self._selected_task is None:
            return
        dialog = TaskDialog(
            self._selected_task,
            available_tasks=self._list_tasks(),
            parent=self,
            config=self.config,
            prompt_template_service=self.prompt_template_service,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            self.task_service.update(
                self._selected_task.id,
                dialog.build_draft(),
            )
        except Exception as exc:
            self._show_error("작업 수정", exc)
            return
        self._notify("작업을 수정했습니다.")
        self.refresh()

    def duplicate_task(self) -> None:
        self._run_selected_action("작업 복제", "duplicate", "작업을 복제했습니다.")

    def run_now_task(self) -> None:
        if self._selected_task is None:
            return
        task_id = self._selected_task.id
        approval_mode = enum_value(getattr(self._selected_task, "approval_mode", ""))
        try:
            self.runtime.reserve_now(task_id)
        except Exception as exc:
            self._show_error("즉시 실행", exc)
            return
        self._prompted_reservations.add(task_id)
        if approval_mode == ApprovalMode.AUTO.value:
            # 사용자가 직접 실행을 눌렀고 작업도 auto 모드이므로 승인 창 없이 실행한다.
            try:
                self.runtime.approve(task_id)
            except Exception as exc:
                self._show_error("즉시 실행", exc)
                return
            self._notify("즉시 실행을 시작했습니다.")
            self.refresh()
            return
        self.request_approval(task_id)

    def move_task_to_top(self) -> None:
        self._run_selected_action(
            "맨 위로 이동",
            "move_to_top",
            "작업을 대기열 맨 위로 이동했습니다.",
        )

    def retry_task(self) -> None:
        self._run_selected_action("재시도", "retry", "작업을 대기열에 다시 넣었습니다.")

    def archive_task(self) -> None:
        self._run_selected_action("완료 보관", "archive", "완료 작업을 보관했습니다.")

    def open_log(self) -> None:
        if self._selected_task is None:
            return
        self.log_viewer.tabs.setCurrentWidget(self.log_viewer.stdout_view)
        self.log_viewer.stdout_view.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.statusBar().showMessage("선택 작업의 최신 실행 로그를 열었습니다.", 3000)

    def open_project_folder(self) -> None:
        if self._selected_task is None:
            return
        project_path = str(getattr(self._selected_task, "project_path", ""))
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(project_path)):
            self._show_error(
                "프로젝트 폴더 열기",
                OSError(f"폴더를 열 수 없습니다: {project_path}"),
            )

    def cancel_task(self) -> None:
        task = self._selected_task
        if task is None:
            return
        title = getattr(task, "title", "")
        if (
            QMessageBox.question(
                self,
                "작업 취소",
                f"'{title}' 작업을 취소하시겠습니까?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            if enum_value(getattr(task, "status", "")) == TaskStatus.RESERVED.value:
                # 예약만 풀면 다시 대기열로 돌아가 재실행되므로 반드시 취소까지 진행한다.
                self.runtime.reject(task.id)
                self.task_service.cancel(task.id)
            else:
                self.task_service.cancel(task.id)
        except Exception as exc:
            self._show_error("작업 취소", exc)
            return
        self._notify("작업을 취소했습니다.")
        self.refresh()

    def cancel_running_task(self) -> None:
        task = self._selected_task
        if task is None:
            return
        if (
            QMessageBox.question(
                self,
                "실행 중단",
                f"'{getattr(task, 'title', '')}' 프로세스를 중단하시겠습니까?",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.runtime.cancel_running(task.id)
        except Exception as exc:
            self._show_error("실행 중단", exc)
            return
        self._notify("실행 중단을 요청했습니다.")
        self.refresh()

    def delete_task(self) -> None:
        task = self._selected_task
        if task is None:
            return
        if (
            QMessageBox.question(
                self,
                "작업 삭제",
                f"'{getattr(task, 'title', '')}' 작업을 삭제하시겠습니까?\n\n"
                "연결된 실행 기록도 함께 삭제됩니다.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        try:
            self.task_service.delete(task.id)
        except Exception as exc:
            self._show_error("작업 삭제", exc)
            return
        self._selected_task = None
        self._notify("작업과 실행 기록을 삭제했습니다.")
        self.refresh()

    def _show_task_context_menu(self, position: object) -> None:
        index = self.task_table.indexAt(position)
        if index.isValid():
            self.task_table.selectRow(index.row())
        menu = QMenu(self.task_table)
        delete_action = menu.addAction("삭제")
        delete_action.setEnabled(self.delete_button.isEnabled())
        delete_action.triggered.connect(self.delete_task)
        menu.exec(self.task_table.viewport().mapToGlobal(position))

    def _run_selected_action(self, title: str, method_name: str, success: str) -> None:
        if self._selected_task is None:
            return
        try:
            getattr(self.task_service, method_name)(self._selected_task.id)
        except Exception as exc:
            self._show_error(title, exc)
            return
        self._notify(success)
        self.refresh()

    def request_approval(self, task_id: int) -> bool:
        """Display a pending confirmation; callable by a runtime/UI polling bridge."""
        # 호출 경로(폴링/즉시 실행)와 무관하게 같은 예약에 두 번 묻지 않도록 먼저 등록한다.
        self._prompted_reservations.add(task_id)
        task = self._get_task(task_id)
        if task is None:
            self.statusBar().showMessage(f"승인할 작업 #{task_id}을 찾을 수 없습니다.")
            return False
        adapter = self._adapter_for_task(task)
        if adapter is None:
            self.statusBar().showMessage("담당 AI 어댑터를 찾을 수 없습니다.")
            return False
        dialog = ApprovalDialog(task, adapter, parent=self)
        self._approval_dialog_open = True
        try:
            approved = dialog.exec() == QDialog.DialogCode.Accepted
        finally:
            self._approval_dialog_open = False
        try:
            if approved:
                self.runtime.approve(task_id)
            else:
                # 거부한 작업은 BLOCKED로 보내 스케줄러가 곧바로 재예약·재질문하지 않게 한다.
                self.runtime.reject(task_id, block=True)
        except Exception as exc:
            self._show_error("실행 승인" if approved else "실행 거부", exc)
            return False
        self._notify(
            "실행을 승인했습니다."
            if approved
            else "실행을 거부했습니다. 작업은 '차단' 상태로 남습니다."
        )
        self.refresh()
        return approved

    def _adapter_for_task(self, task: object) -> object | None:
        raw_agent = getattr(task, "assigned_agent", None) or getattr(task, "agent_policy", None)
        value = enum_value(raw_agent)
        for key in (raw_agent, value):
            if key in self.adapters:
                return self.adapters[key]
        return next(
            (
                adapter
                for adapter in self.adapters.values()
                if enum_value(getattr(adapter, "name", "")) == value
            ),
            None,
        )

    def _adapter_for_agent(self, agent: AgentName) -> object | None:
        for key in (agent, agent.value):
            if key in self.adapters:
                return self.adapters[key]
        return next(
            (
                adapter
                for adapter in self.adapters.values()
                if enum_value(getattr(adapter, "name", "")) == agent.value
            ),
            None,
        )

    def _set_agent_availability(
        self,
        agent: AgentName,
        status: AgentStatus,
        available_at: datetime | None,
    ) -> None:
        try:
            self.agent_service.set_availability(agent, status, available_at)
        except Exception as exc:
            self._show_error("AI 상태 변경", exc)
            return
        self._notify(f"{agent.value.title()} 상태를 변경했습니다.")
        self.refresh()

    def _set_tray_availability(self, agent: AgentName, status: AgentStatus) -> None:
        self._set_agent_availability(agent, status, None)

    def show_from_tray(self) -> None:
        self.show()
        self.raise_()
        self.activateWindow()

    def _quit_application(self) -> None:
        active_tasks = self._tasks_requiring_exit_warning()
        if active_tasks and (
            QMessageBox.question(
                self,
                "AI Queue Runner 종료",
                "정말 종료하시겠습니까? 진행 중인 작업은 모두 중단됩니다.",
            )
            != QMessageBox.StandardButton.Yes
        ):
            return
        for task in active_tasks:
            try:
                self.runtime.cancel_running(task.id)
            except Exception as exc:
                self._show_error("종료", exc)
                return
        shutdown = getattr(self.runtime, "shutdown", None)
        if callable(shutdown):
            shutdown()
        self.refresh_timer.stop()
        application = QApplication.instance()
        if application is not None:
            application.quit()

    def _tasks_requiring_exit_warning(self) -> list[object]:
        active_tasks = []
        for task in self._list_tasks():
            status = enum_value(getattr(task, "status", ""))
            if status == TaskStatus.RUNNING.value:
                active_tasks.append(task)
                continue
            if status != TaskStatus.RESERVED.value:
                continue
            try:
                active = bool(self.runtime.is_active(task.id))
            except (AttributeError, TypeError):
                active = False
            if active:
                active_tasks.append(task)
        return active_tasks

    def closeEvent(self, event: Any) -> None:  # noqa: N802
        if self.tray is not None and self.tray.isVisible():
            self.hide()
            self.tray.notify("AI Queue Runner", "백그라운드에서 계속 실행 중입니다.")
            event.ignore()
            return
        super().closeEvent(event)
