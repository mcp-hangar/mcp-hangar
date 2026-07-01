"""Unit tests for the fail-closed :class:`TaskDigestGuard`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_digest_guard import TaskDigestGuard


def test_pin_and_verify_match() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    assert guard.verify("task-1", "sha256:abc") is True


def test_drift_mismatch_fail_closed() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    # Tool schema drifted between invoke and completion: digests differ.
    assert guard.verify("task-1", "sha256:def") is False


def test_unknown_task_id_fail_closed() -> None:
    guard = TaskDigestGuard()
    assert guard.verify("never-pinned", "sha256:abc") is False


def test_empty_task_id_pin_raises() -> None:
    guard = TaskDigestGuard()
    with pytest.raises(ValueError):
        guard.pin("", "sha256:abc")


def test_empty_digest_pin_raises() -> None:
    guard = TaskDigestGuard()
    with pytest.raises(ValueError):
        guard.pin("task-1", "")


def test_empty_task_id_verify_fail_closed() -> None:
    guard = TaskDigestGuard()
    assert guard.verify("", "sha256:abc") is False


def test_empty_observed_digest_verify_fail_closed() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    assert guard.verify("task-1", "") is False


def test_expired_entry_fail_closed() -> None:
    # Zero TTL: any elapsed time expires the entry, so verification fails closed.
    guard = TaskDigestGuard(ttl=0.0)
    guard.pin("task-1", "sha256:abc")
    assert guard.verify("task-1", "sha256:abc") is False


def test_repin_refreshes_digest() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    guard.pin("task-1", "sha256:def")
    assert guard.verify("task-1", "sha256:def") is True
    assert guard.verify("task-1", "sha256:abc") is False


def test_discard_removes() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    guard.discard("task-1")
    assert guard.verify("task-1", "sha256:abc") is False
    # Discarding an unknown id is a no-op.
    guard.discard("task-1")


def test_clear_removes_all() -> None:
    guard = TaskDigestGuard()
    guard.pin("task-1", "sha256:abc")
    guard.pin("task-2", "sha256:def")
    guard.clear()
    assert guard.verify("task-1", "sha256:abc") is False
    assert guard.verify("task-2", "sha256:def") is False


def test_lru_eviction_on_maxsize() -> None:
    guard = TaskDigestGuard(maxsize=2)
    guard.pin("task-1", "sha256:a")
    guard.pin("task-2", "sha256:b")
    guard.pin("task-3", "sha256:c")  # Evicts the oldest (task-1).
    assert guard.verify("task-1", "sha256:a") is False
    assert guard.verify("task-2", "sha256:b") is True
    assert guard.verify("task-3", "sha256:c") is True
