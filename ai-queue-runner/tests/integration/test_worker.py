from __future__ import annotations

import signal
import threading
import time
from pathlib import Path

import pytest

from ai_queue_runner.application.worker import run_worker
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.infrastructure.runtime_owner import (
    RuntimeLease,
    RuntimeLeaseError,
    runtime_lease_path,
)


def make_config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.app.allowed_project_roots = [tmp_path]
    config.app.db_path = tmp_path / "queue.db"
    config.app.logs_dir = tmp_path / "logs"
    return config


def test_run_worker_initializes_db_and_returns_zero_on_stop(tmp_path):
    config = make_config(tmp_path)
    stop_event = threading.Event()
    stop_event.set()

    assert run_worker(config, stop_event=stop_event) == 0
    assert config.app.db_path.exists()


def test_run_worker_holds_the_lease_while_running_and_releases_it_after(tmp_path):
    config = make_config(tmp_path)
    stop_event = threading.Event()
    result: dict[str, int] = {}

    def target() -> None:
        result["code"] = run_worker(config, stop_event=stop_event)

    thread = threading.Thread(target=target)
    thread.start()
    try:
        lease_path = runtime_lease_path(config.app.db_path)
        # Poll ownership with a non-mutating probe first: a competing acquire()
        # here would race the worker's own acquire() and could make the worker
        # itself fail with RuntimeLeaseError before it ever starts.
        probe = RuntimeLease(lease_path, mode="probe")
        deadline = time.time() + 5
        owner = None
        while time.time() < deadline:
            owner = probe.external_owner()
            if owner is not None:
                break
            time.sleep(0.02)
        assert owner is not None and owner.mode == "worker", "worker never acquired the lease"

        contender = RuntimeLease(lease_path, mode="contender")
        with pytest.raises(RuntimeLeaseError):
            contender.acquire()
    finally:
        stop_event.set()
        thread.join(timeout=5)

    assert not thread.is_alive()
    assert result["code"] == 0

    # A new owner must be able to acquire the lease once the worker has shut down.
    released = RuntimeLease(lease_path, mode="after")
    released.acquire()
    released.release()


def test_run_worker_installs_and_restores_signal_handlers(tmp_path):
    """With no stop_event, run_worker must install its own SIGINT/SIGTERM
    handlers, react to a real signal, and restore the previous handlers
    before returning."""
    config = make_config(tmp_path)
    original_int = signal.getsignal(signal.SIGINT)
    original_term = signal.getsignal(signal.SIGTERM)
    observed: dict[str, object] = {}

    def send_sigint_once_installed() -> None:
        deadline = time.time() + 5
        while signal.getsignal(signal.SIGINT) is original_int and time.time() < deadline:
            time.sleep(0.01)
        observed["sigint_during_run"] = signal.getsignal(signal.SIGINT)
        observed["sigterm_during_run"] = signal.getsignal(signal.SIGTERM)
        signal.raise_signal(signal.SIGINT)

    helper = threading.Thread(target=send_sigint_once_installed)
    helper.start()
    try:
        assert run_worker(config) == 0
    finally:
        helper.join(timeout=5)

    assert not helper.is_alive()
    assert observed["sigint_during_run"] is not original_int
    assert observed["sigterm_during_run"] is not original_term
    assert signal.getsignal(signal.SIGINT) is original_int
    assert signal.getsignal(signal.SIGTERM) is original_term
