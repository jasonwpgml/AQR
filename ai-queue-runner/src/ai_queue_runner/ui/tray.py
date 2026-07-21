"""System-tray controls and notification fallback."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QApplication, QMenu, QStyle, QSystemTrayIcon, QWidget

from ai_queue_runner.domain.enums import AgentName, AgentStatus


class QueueSystemTray(QSystemTrayIcon):
    """Small operational menu mirroring the core scheduler controls."""

    show_requested = Signal()
    start_requested = Signal()
    pause_requested = Signal()
    availability_requested = Signal(object, object)
    quit_requested = Signal()

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        fallback: Callable[[str], None] | None = None,
    ) -> None:
        icon = QApplication.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        super().__init__(icon, parent)
        self._fallback = fallback
        self.setToolTip("AI Queue Runner")

        self.menu = QMenu(parent)
        self.show_action = self.menu.addAction("AI Queue Runner 열기")
        self.show_action.triggered.connect(self.show_requested)
        self.menu.addSeparator()
        self.start_action = self.menu.addAction("스케줄러 시작")
        self.start_action.triggered.connect(self.start_requested)
        self.pause_action = self.menu.addAction("일시정지")
        self.pause_action.triggered.connect(self.pause_requested)

        self.next_task_action = QAction("다음 작업: 없음", self.menu)
        self.next_task_action.setEnabled(False)
        self.menu.addAction(self.next_task_action)

        for agent in AgentName:
            submenu = self.menu.addMenu(f"{agent.value.title()} 상태")
            available = submenu.addAction("사용 가능")
            available.triggered.connect(
                lambda _checked=False, a=agent: self.availability_requested.emit(
                    a, AgentStatus.AVAILABLE
                )
            )
            cooldown = submenu.addAction("쿨다운")
            cooldown.triggered.connect(
                lambda _checked=False, a=agent: self.availability_requested.emit(
                    a, AgentStatus.COOLDOWN
                )
            )

        self.menu.addSeparator()
        self.quit_action = self.menu.addAction("종료")
        self.quit_action.triggered.connect(self.quit_requested)
        self.setContextMenu(self.menu)
        self.activated.connect(self._on_activated)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.show_requested.emit()

    def set_running(self, running: bool) -> None:
        self.start_action.setEnabled(not running)
        self.pause_action.setEnabled(running)

    def set_next_task(self, task: object | None) -> None:
        title = getattr(task, "title", None)
        self.next_task_action.setText(f"다음 작업: {title}" if title else "다음 작업: 없음")

    def notify(self, title: str, message: str) -> None:
        if self.isSystemTrayAvailable() and self.isVisible():
            self.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)
        elif self._fallback is not None:
            self._fallback(f"{title}: {message}")
