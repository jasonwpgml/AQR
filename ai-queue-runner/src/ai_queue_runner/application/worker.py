'''Headless queue worker process composition.'''

from __future__ import annotations

import signal
import threading

from ai_queue_runner.application.runtime import QueueRuntime
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.infrastructure.adapters import ClaudeAdapter, CodexAdapter
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)
from ai_queue_runner.infrastructure.runtime_owner import RuntimeLease, runtime_lease_path


def run_worker(config: AppConfig, *, stop_event: threading.Event | None = None) -> int:
    '''Run automatic queue work until a termination signal is received.'''
    engine = create_db_engine(config.app.db_path)
    init_db(engine)
    session_factory = make_session_factory(engine)
    adapters = {
        'claude': ClaudeAdapter(config.agents['claude']),
        'codex': CodexAdapter(config.agents['codex']),
    }
    runtime = QueueRuntime(
        session_factory,
        config,
        adapters,
        lease=RuntimeLease(runtime_lease_path(config.app.db_path), mode='worker'),
        auto_approval_only=True,
    )
    event = stop_event or threading.Event()
    previous_handlers: dict[signal.Signals, object] = {}
    if stop_event is None:
        for signal_name in ('SIGINT', 'SIGTERM'):
            signal_value = getattr(signal, signal_name, None)
            if signal_value is None:
                continue
            previous_handlers[signal_value] = signal.getsignal(signal_value)
            signal.signal(signal_value, lambda *_args: event.set())
    try:
        runtime.start()
        event.wait()
        return 0
    finally:
        runtime.shutdown(wait=True)
        engine.dispose()
        for signal_value, handler in previous_handlers.items():
            signal.signal(signal_value, handler)
