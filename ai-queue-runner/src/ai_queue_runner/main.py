"""Application entry point."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from ai_queue_runner import __version__
from ai_queue_runner.config.loader import ConfigError, load_config
from ai_queue_runner.infrastructure.logging_config import setup_logging

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ai-queue-runner",
        description="Local task queue scheduler for Claude and Codex CLIs",
    )
    parser.add_argument("--config", type=Path, default=None, help="path to config.toml")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2

    setup_logging(config.app.logs_dir, config.app.log_level)
    logger.info("AI Queue Runner %s starting", __version__)
    logger.info(
        "global_auto_run=%s agents=%s",
        config.app.global_auto_run,
        ",".join(config.agents),
    )

    # AIDEV-TODO: Phase 5 replaces this stub with the PySide6 app bootstrap.
    print(f"AI Queue Runner {__version__}: configuration OK, nothing to run yet (Phase 0).")
    return 0
