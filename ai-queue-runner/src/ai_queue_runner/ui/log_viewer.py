"""Polling stdout/stderr viewer for one persisted task run."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QLabel, QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from ai_queue_runner.application.log_rendering import render_pretty

_NO_RUN = "실행 기록이 없습니다."
_NO_FILE = "로그 파일이 없습니다."
_MAX_VISIBLE_LOG_BYTES = 2 * 1024 * 1024
_TRUNCATED = "[이전 로그는 생략되었습니다. 파일에는 전체 내용이 보존됩니다.]\n"


class LogViewer(QWidget):
    """Read run log files on a timer; no repository access occurs here."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        poll_interval_ms: int = 1000,
    ) -> None:
        super().__init__(parent)
        self._run: object | None = None
        self._agent: str | None = None
        self.run_label = QLabel(_NO_RUN)
        self.raw_checkbox = QCheckBox("원본 보기")
        self.stdout_view = self._new_text_view()
        self.stderr_view = self._new_text_view()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.stdout_view, "표준 출력")
        self.tabs.addTab(self.stderr_view, "오류 출력")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.run_label)
        layout.addWidget(self.raw_checkbox)
        layout.addWidget(self.tabs, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(poll_interval_ms)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.raw_checkbox.toggled.connect(self.refresh)
        self.clear()

    @staticmethod
    def _new_text_view() -> QPlainTextEdit:
        view = QPlainTextEdit()
        view.setReadOnly(True)
        view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = view.font()
        font.setFamily("Consolas")
        view.setFont(font)
        return view

    def set_run(self, run: object | None) -> None:
        self._run = run
        if run is None:
            self.clear()
            return
        attempt = getattr(run, "attempt", "?")
        agent = getattr(getattr(run, "agent", ""), "value", getattr(run, "agent", ""))
        self._agent = str(agent) or None
        exit_code = getattr(run, "exit_code", None)
        exit_text = "실행 중" if exit_code is None else f"종료 코드 {exit_code}"
        worktree_branch = getattr(run, "worktree_branch", None)
        worktree_path = getattr(run, "worktree_path", None)
        worktree_text = (
            f" · {worktree_branch} · {worktree_path}"
            if worktree_branch and worktree_path
            else ""
        )
        self.run_label.setText(
            f"시도 {attempt} · {str(agent).title()} · {exit_text}{worktree_text}"
        )
        self.refresh()

    def clear(self) -> None:
        self._run = None
        self._agent = None
        self.run_label.setText(_NO_RUN)
        self.stdout_view.setPlainText(_NO_RUN)
        self.stderr_view.setPlainText(_NO_RUN)

    def refresh(self) -> None:
        if self._run is None:
            return
        self._set_file_text(self.stdout_view, getattr(self._run, "stdout_path", None))
        self._set_file_text(self.stderr_view, getattr(self._run, "stderr_path", None))

    def _set_file_text(self, view: QPlainTextEdit, raw_path: object) -> None:
        if not raw_path:
            text = _NO_FILE
        else:
            path = Path(str(raw_path))
            try:
                raw_text = _read_visible_tail(path) if path.is_file() else _NO_FILE
                text = (
                    raw_text
                    if self.raw_checkbox.isChecked()
                    else render_pretty(raw_text, agent=self._agent)
                )
            except OSError as exc:
                text = f"로그를 읽을 수 없습니다: {exc}"
        if view.toPlainText() == text:
            return
        scrollbar = view.verticalScrollBar()
        was_at_end = scrollbar.value() >= scrollbar.maximum() - 2
        view.setPlainText(text)
        if was_at_end:
            scrollbar.setValue(scrollbar.maximum())

    def start_polling(self) -> None:
        self.timer.start()

    def stop_polling(self) -> None:
        self.timer.stop()


def _read_visible_tail(path: Path) -> str:
    with path.open("rb") as stream:
        stream.seek(0, 2)
        size = stream.tell()
        truncated = size > _MAX_VISIBLE_LOG_BYTES
        if truncated:
            stream.seek(-_MAX_VISIBLE_LOG_BYTES, 2)
        else:
            stream.seek(0)
        text = stream.read().decode("utf-8", errors="replace")
    return f"{_TRUNCATED}{text}" if truncated else text
