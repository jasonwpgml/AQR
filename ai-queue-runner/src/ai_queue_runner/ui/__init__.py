"""Native PySide6 desktop widgets for AI Queue Runner."""

from ai_queue_runner.ui.agent_status_widget import AgentStatusWidget
from ai_queue_runner.ui.approval_dialog import ApprovalDialog
from ai_queue_runner.ui.log_viewer import LogViewer
from ai_queue_runner.ui.main_window import MainWindow
from ai_queue_runner.ui.task_dialog import TaskDialog
from ai_queue_runner.ui.task_table import TaskTableModel
from ai_queue_runner.ui.tray import QueueSystemTray

__all__ = [
    "AgentStatusWidget",
    "ApprovalDialog",
    "LogViewer",
    "MainWindow",
    "QueueSystemTray",
    "TaskDialog",
    "TaskTableModel",
]
