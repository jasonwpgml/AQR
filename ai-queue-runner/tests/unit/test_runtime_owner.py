from pathlib import Path

import pytest

from ai_queue_runner.infrastructure.runtime_owner import (
    RuntimeLease,
    RuntimeLeaseError,
    runtime_lease_path,
)


def test_runtime_lease_exposes_owner_and_prevents_duplicate_scheduler(
    tmp_path: Path,
) -> None:
    path = runtime_lease_path(tmp_path / "queue.db")
    worker = RuntimeLease(path, mode="worker")
    observer = RuntimeLease(path, mode="gui")

    owner = worker.acquire()
    assert owner.mode == "worker"
    assert observer.external_owner() == owner
    with pytest.raises(RuntimeLeaseError, match="worker"):
        observer.acquire()

    worker.release()
    assert observer.external_owner() is None
    observer.acquire()
    observer.release()


def test_runtime_lease_release_is_idempotent(tmp_path: Path) -> None:
    lease = RuntimeLease(runtime_lease_path(tmp_path / "queue.db"), mode="gui")
    lease.acquire()
    lease.release()
    lease.release()
