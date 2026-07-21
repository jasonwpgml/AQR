"""Settings dialog for the project-path execution policy."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.config.exporter import export_config


class SettingsDialog(QDialog):
    """Edit allowed project roots without mutating config until save."""

    def __init__(
        self,
        config: AppConfig,
        active_config_path: Path | str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.active_config_path = Path(active_config_path)

        self.setWindowTitle("설정")
        self.resize(620, 380)

        description = QLabel("작업 실행을 허용할 프로젝트 루트 폴더를 관리합니다.")
        self.roots_list = QListWidget()
        self.roots_list.setAccessibleName("허용 프로젝트 루트")
        for root in config.app.allowed_project_roots:
            self.roots_list.addItem(str(root))

        self.warning_label = QLabel("허용 루트가 없으면 모든 작업 실행이 차단됩니다")
        self.warning_label.setStyleSheet("color: #b45309;")

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        self.add_button = QPushButton("폴더 추가")
        self.remove_button = QPushButton("선택 제거")
        self.save_button = QPushButton("저장")
        self.cancel_button = QPushButton("취소")

        self.add_button.clicked.connect(self._choose_root)
        self.remove_button.clicked.connect(self.remove_selected_root)
        self.save_button.clicked.connect(self.save)
        self.cancel_button.clicked.connect(self.reject)

        root_controls = QHBoxLayout()
        root_controls.addWidget(self.add_button)
        root_controls.addWidget(self.remove_button)
        root_controls.addStretch(1)

        dialog_controls = QHBoxLayout()
        dialog_controls.addStretch(1)
        dialog_controls.addWidget(self.save_button)
        dialog_controls.addWidget(self.cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(description)
        layout.addWidget(self.roots_list, 1)
        layout.addLayout(root_controls)
        layout.addWidget(self.warning_label)
        layout.addWidget(self.status_label)
        layout.addLayout(dialog_controls)

        self._update_warning()

    def _choose_root(self) -> None:
        selected = QFileDialog.getExistingDirectory(self, "허용할 프로젝트 루트 선택")
        if selected:
            self.add_root(selected)

    def add_root(self, value: Path | str) -> bool:
        """Validate and add a root; exposed for non-native-picker UI tests."""
        candidate = Path(value).expanduser()
        if not candidate.is_absolute():
            self._show_validation_error("절대 경로인 폴더를 선택하세요.")
            return False
        try:
            candidate = candidate.resolve(strict=True)
        except OSError:
            self._show_validation_error("존재하는 폴더를 선택하세요.")
            return False
        if not candidate.is_dir():
            self._show_validation_error("존재하는 폴더를 선택하세요.")
            return False

        if candidate in self._resolved_roots():
            self._show_validation_error("이미 추가된 폴더입니다.")
            return False

        self.roots_list.addItem(str(candidate))
        self.status_label.clear()
        self._update_warning()
        return True

    def remove_selected_root(self) -> None:
        for item in self.roots_list.selectedItems():
            self.roots_list.takeItem(self.roots_list.row(item))
        self.status_label.clear()
        self._update_warning()

    def save(self) -> None:
        roots = [Path(self.roots_list.item(row).text()) for row in range(self.roots_list.count())]
        original_roots = list(self.config.app.allowed_project_roots)
        try:
            # AIDEV-NOTE: keep this in-place so services sharing AppConfig see policy changes live.
            self.config.app.allowed_project_roots[:] = roots
            export_config(self.config, self.active_config_path)
        except (OSError, TypeError, ValueError) as exc:
            self.config.app.allowed_project_roots[:] = original_roots
            self._show_validation_error(f"설정을 저장하지 못했습니다: {exc}")
            return

        self.status_label.setText(f"설정을 저장했습니다: {self.active_config_path}")
        self.accept()

    def _resolved_roots(self) -> set[Path]:
        resolved: set[Path] = set()
        for row in range(self.roots_list.count()):
            root = Path(self.roots_list.item(row).text()).expanduser()
            try:
                resolved.add(root.resolve(strict=True))
            except OSError:
                continue
        return resolved

    def _show_validation_error(self, message: str) -> None:
        self.status_label.setText(message)

    def _update_warning(self) -> None:
        self.warning_label.setVisible(self.roots_list.count() == 0)
