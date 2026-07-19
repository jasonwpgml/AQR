'''Cross-process ownership for the single background queue scheduler.'''

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO


class RuntimeLeaseError(RuntimeError):
    '''Another process already owns the queue scheduler.'''


@dataclass(frozen=True)
class RuntimeOwner:
    mode: str
    pid: int


class RuntimeLease:
    '''Hold an OS-released file lock while one process schedules queue work.'''

    _registry_lock = threading.Lock()
    _process_registry: set[Path] = set()

    def __init__(self, path: Path, *, mode: str) -> None:
        self.path = path.resolve()
        self.mode = mode
        self._file: BinaryIO | None = None

    def acquire(self) -> RuntimeOwner:
        if self._file is not None:
            return RuntimeOwner(self.mode, os.getpid())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open('a+b')
        self._ensure_byte(handle)
        with self._registry_lock:
            conflict = (
                self.path in self._process_registry or not self._try_lock(handle)
            )
            if not conflict:
                self._process_registry.add(self.path)
        if conflict:
            handle.close()
            owner = self.external_owner()
            detail = f' ({owner.mode}, PID {owner.pid})' if owner else ''
            raise RuntimeLeaseError(f'queue scheduler is already running{detail}')
        owner = RuntimeOwner(self.mode, os.getpid())
        try:
            handle.seek(1)
            handle.truncate()
            handle.write(json.dumps(owner.__dict__).encode('utf-8'))
            handle.flush()
            os.fsync(handle.fileno())
        except Exception:
            with self._registry_lock:
                self._process_registry.discard(self.path)
            self._unlock(handle)
            handle.close()
            raise
        self._file = handle
        return owner

    def release(self) -> None:
        handle, self._file = self._file, None
        if handle is None:
            return
        with self._registry_lock:
            self._process_registry.discard(self.path)
        try:
            self._unlock(handle)
        finally:
            handle.close()

    def external_owner(self) -> RuntimeOwner | None:
        if self._file is not None:
            return None
        with self._registry_lock:
            owned_in_process = self.path in self._process_registry
        if owned_in_process:
            return self._read_owner()
        if not self.path.exists():
            return None
        handle = self.path.open('a+b')
        self._ensure_byte(handle)
        if self._try_lock(handle):
            self._unlock(handle)
            handle.close()
            return None
        handle.close()
        return self._read_owner() or RuntimeOwner('unknown', 0)

    def _read_owner(self) -> RuntimeOwner | None:
        try:
            with self.path.open('rb') as handle:
                handle.seek(1)
                data = json.loads(handle.read().decode('utf-8'))
            return RuntimeOwner(mode=str(data['mode']), pid=int(data['pid']))
        except (KeyError, OSError, TypeError, UnicodeDecodeError, ValueError):
            return None

    @staticmethod
    def _ensure_byte(handle: BinaryIO) -> None:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(bytes(1))
            handle.flush()

    @staticmethod
    def _try_lock(handle: BinaryIO) -> bool:
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlock(handle: BinaryIO) -> None:
        handle.seek(0)
        if os.name == 'nt':
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def runtime_lease_path(db_path: Path) -> Path:
    return db_path.with_name(f'{db_path.name}.runtime.lock')
