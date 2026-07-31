"""Unit tests for the fail-closed :class:`TaskDigestGuard`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_digest_guard import (
    TaskDigestConflictError,
    TaskDigestGuard,
)

# Composite keys are (target_server_id, task_id).
_K1 = ("srv-a", "task-1")
_K2 = ("srv-a", "task-2")
_K3 = ("srv-a", "task-3")


def test_pin_and_verify_match() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    assert guard.verify(_K1, "sha256:abc") is True


def test_same_task_id_different_server_is_distinct() -> None:
    """task_id is unique only per upstream: identically named tasks on two
    servers pin independently."""
    guard = TaskDigestGuard()
    guard.pin(("srv-a", "task-1"), "sha256:aaa")
    guard.pin(("srv-b", "task-1"), "sha256:bbb")
    assert guard.verify(("srv-a", "task-1"), "sha256:aaa") is True
    assert guard.verify(("srv-b", "task-1"), "sha256:bbb") is True
    # A server's digest must not verify another server's identically named task.
    assert guard.verify(("srv-b", "task-1"), "sha256:aaa") is False


def test_drift_mismatch_fail_closed() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    # Tool schema drifted between invoke and completion: digests differ.
    assert guard.verify(_K1, "sha256:def") is False


def test_unknown_task_id_fail_closed() -> None:
    guard = TaskDigestGuard()
    assert guard.verify(("srv-a", "never-pinned"), "sha256:abc") is False


def test_empty_task_id_pin_raises() -> None:
    guard = TaskDigestGuard()
    with pytest.raises(ValueError):
        guard.pin(("srv-a", ""), "sha256:abc")


def test_empty_server_id_pin_raises() -> None:
    guard = TaskDigestGuard()
    with pytest.raises(ValueError):
        guard.pin(("", "task-1"), "sha256:abc")


def test_empty_digest_pin_raises() -> None:
    guard = TaskDigestGuard()
    with pytest.raises(ValueError):
        guard.pin(_K1, "")


def test_empty_task_id_verify_fail_closed() -> None:
    guard = TaskDigestGuard()
    assert guard.verify(("srv-a", ""), "sha256:abc") is False


def test_empty_observed_digest_verify_fail_closed() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    assert guard.verify(_K1, "") is False


def test_expired_entry_fail_closed() -> None:
    # Zero TTL: any elapsed time expires the entry, so verification fails closed.
    guard = TaskDigestGuard(ttl=0.0)
    guard.pin(_K1, "sha256:abc")
    assert guard.verify(_K1, "sha256:abc") is False


def test_repin_same_digest_is_idempotent() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    guard.pin(_K1, "sha256:abc")  # Same digest: refresh, no raise.
    assert guard.verify(_K1, "sha256:abc") is True


def test_repin_different_digest_fails_closed() -> None:
    """Re-pinning a live key to a different digest must fail closed, not clobber."""
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    with pytest.raises(TaskDigestConflictError):
        guard.pin(_K1, "sha256:def")
    # Original pin is preserved.
    assert guard.verify(_K1, "sha256:abc") is True
    assert guard.verify(_K1, "sha256:def") is False


def test_conflict_error_is_value_error() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    with pytest.raises(ValueError):  # TaskDigestConflictError subclasses ValueError.
        guard.pin(_K1, "sha256:def")


def test_discard_removes() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    guard.discard(_K1)
    assert guard.verify(_K1, "sha256:abc") is False
    # Discarding an unknown id is a no-op.
    guard.discard(_K1)


def test_discard_does_not_fire_on_evict() -> None:
    evicted: list[tuple[str, str]] = []
    guard = TaskDigestGuard(on_evict=evicted.append)
    guard.pin(_K1, "sha256:abc")
    guard.discard(_K1)
    assert evicted == []  # Deliberate terminal removal: no eviction callback.


def test_clear_removes_all() -> None:
    guard = TaskDigestGuard()
    guard.pin(_K1, "sha256:abc")
    guard.pin(_K2, "sha256:def")
    guard.clear()
    assert guard.verify(_K1, "sha256:abc") is False
    assert guard.verify(_K2, "sha256:def") is False


def test_lru_eviction_on_maxsize() -> None:
    guard = TaskDigestGuard(maxsize=2)
    guard.pin(_K1, "sha256:a")
    guard.pin(_K2, "sha256:b")
    guard.pin(_K3, "sha256:c")  # Evicts the oldest (_K1).
    assert guard.verify(_K1, "sha256:a") is False
    assert guard.verify(_K2, "sha256:b") is True
    assert guard.verify(_K3, "sha256:c") is True


def test_on_evict_fires_on_lru_eviction() -> None:
    """Filling past the cap evicts a still-live entry; the callback must receive
    its key so the caller (governance ledger) can fail-close the task."""
    evicted: list[tuple[str, str]] = []
    guard = TaskDigestGuard(maxsize=2, on_evict=evicted.append)
    guard.pin(_K1, "sha256:a")
    guard.pin(_K2, "sha256:b")
    guard.pin(_K3, "sha256:c")  # Evicts oldest live entry (_K1).
    assert evicted == [_K1]


def test_on_evict_fires_on_lazy_ttl_expiry() -> None:
    """An entry found expired on access is evicted and fires the callback."""
    evicted: list[tuple[str, str]] = []
    guard = TaskDigestGuard(ttl=0.0, on_evict=evicted.append)
    guard.pin(_K1, "sha256:abc")
    assert guard.verify(_K1, "sha256:abc") is False  # Expired -> deny + evict.
    assert evicted == [_K1]


def test_on_evict_failure_does_not_break_guard() -> None:
    def boom(_key: tuple[str, str]) -> None:
        raise RuntimeError("ledger down")

    guard = TaskDigestGuard(maxsize=1, on_evict=boom)
    guard.pin(_K1, "sha256:a")
    guard.pin(_K2, "sha256:b")  # Evicts _K1; callback raises but is swallowed.
    assert guard.verify(_K2, "sha256:b") is True
