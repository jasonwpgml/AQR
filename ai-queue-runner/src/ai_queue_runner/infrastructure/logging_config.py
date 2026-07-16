"""Application-wide logging setup: console + rotating file handler."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

_configured = False


def setup_logging(logs_dir: Path, level: str = "INFO") -> None:
    global _configured
    if _configured:
        return

    logs_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.handlers.RotatingFileHandler(
        logs_dir / "app.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    console_handler = logging.StreamHandler()

    formatter = logging.Formatter(LOG_FORMAT)
    for handler in (file_handler, console_handler):
        handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    _configured = True
