"""Unit tests for the fail-closed :class:`TaskConsentGate`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_consent import ConsentKey, TaskConsentGate

# Composite task keys: (target_server_id, task_id).
_T1 = ("srv-a", "task-1")
_T2 = ("srv-a", "task-2")
_T3 = ("srv-a", "task-3")


def test_open_then_answer_matches() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    assert gate.answer(_T1, "field-a") is True


def test_answer_clears_pending_consent() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    assert gate.is_consent_pending(_T1, "field-a") is True
    assert gate.answer(_T1, "field-a") is True
    # Consent was consumed by the answer.
    assert gate.is_consent_pending(_T1, "field-a") is False


def test_second_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    assert gate.answer(_T1, "field-a") is True
    # A replayed answer has no pending consent to match.
    assert gate.answer(_T1, "field-a") is False


def test_answer_with_no_pending_fail_closed() -> None:
    gate = TaskConsentGate()
    # No consent was ever opened: an injected answer is rejected.
    assert gate.answer(_T1, "field-a") is False


def test_unknown_task_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    assert gate.answer(_T2, "field-a") is False


def test_unknown_input_key_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    assert gate.answer(_T1, "field-b") is False


def test_same_task_id_different_server_is_distinct() -> None:
    # Task ids are unique only per upstream: the same task_id from two different
    # target servers records independent consents that do not collide.
    gate = TaskConsentGate()
    gate.open(("srv-a", "task-1"), "field-a")
    gate.open(("srv-b", "task-1"), "field-a")
    assert gate.is_consent_pending(("srv-a", "task-1"), "field-a") is True
    assert gate.is_consent_pending(("srv-b", "task-1"), "field-a") is True
    # Answering one leaves the other untouched.
    assert gate.answer(("srv-a", "task-1"), "field-a") is True
    assert gate.is_consent_pending(("srv-a", "task-1"), "field-a") is False
    assert gate.is_consent_pending(("srv-b", "task-1"), "field-a") is True


def test_empty_server_id_open_raises() -> None:
    gate = TaskConsentGate()
    with pytest.raises(ValueError):
        gate.open(("", "task-1"), "field-a")


def test_empty_task_id_open_raises() -> None:
    gate = TaskConsentGate()
    with pytest.raises(ValueError):
        gate.open(("srv-a", ""), "field-a")


def test_empty_input_key_open_raises() -> None:
    gate = TaskConsentGate()
    with pytest.raises(ValueError):
        gate.open(_T1, "")


def test_empty_args_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    assert gate.answer(("", "task-1"), "field-a") is False
    assert gate.answer(("srv-a", ""), "field-a") is False
    assert gate.answer(_T1, "") is False


def test_empty_args_is_consent_pending_fail_closed() -> None:
    gate = TaskConsentGate()
    assert gate.is_consent_pending(("", "task-1"), "field-a") is False
    assert gate.is_consent_pending(("srv-a", ""), "field-a") is False
    assert gate.is_consent_pending(_T1, "") is False


def test_is_consent_pending_transitions() -> None:
    gate = TaskConsentGate()
    assert gate.is_consent_pending(_T1, "field-a") is False
    gate.open(_T1, "field-a")
    assert gate.is_consent_pending(_T1, "field-a") is True
    _ = gate.answer(_T1, "field-a")
    assert gate.is_consent_pending(_T1, "field-a") is False


def test_discard_removes_all_task_inputs() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    gate.open(_T1, "field-b")
    gate.open(_T2, "field-a")
    gate.discard(_T1)
    assert gate.is_consent_pending(_T1, "field-a") is False
    assert gate.is_consent_pending(_T1, "field-b") is False
    # Other tasks are untouched.
    assert gate.is_consent_pending(_T2, "field-a") is True


def test_discard_same_task_id_other_server_untouched() -> None:
    # Discard is scoped by the full composite task key, not the bare task_id.
    gate = TaskConsentGate()
    gate.open(("srv-a", "task-1"), "field-a")
    gate.open(("srv-b", "task-1"), "field-a")
    gate.discard(("srv-a", "task-1"))
    assert gate.is_consent_pending(("srv-a", "task-1"), "field-a") is False
    assert gate.is_consent_pending(("srv-b", "task-1"), "field-a") is True


def test_clear_empties_gate() -> None:
    gate = TaskConsentGate()
    gate.open(_T1, "field-a")
    gate.open(_T2, "field-b")
    gate.clear()
    assert gate.is_consent_pending(_T1, "field-a") is False
    assert gate.is_consent_pending(_T2, "field-b") is False


def test_ttl_expiry_makes_consent_unanswerable() -> None:
    # Zero TTL: any elapsed time expires the pending consent, so the answer
    # is rejected fail-closed.
    gate = TaskConsentGate(ttl=0.0)
    gate.open(_T1, "field-a")
    assert gate.answer(_T1, "field-a") is False


def test_ttl_expiry_makes_consent_not_pending() -> None:
    gate = TaskConsentGate(ttl=0.0)
    gate.open(_T1, "field-a")
    assert gate.is_consent_pending(_T1, "field-a") is False


def test_lru_eviction_on_maxsize() -> None:
    gate = TaskConsentGate(maxsize=2)
    gate.open(_T1, "field-a")
    gate.open(_T2, "field-a")
    gate.open(_T3, "field-a")  # Evicts the oldest (_T1, field-a).
    assert gate.is_consent_pending(_T1, "field-a") is False
    assert gate.is_consent_pending(_T2, "field-a") is True
    assert gate.is_consent_pending(_T3, "field-a") is True


def test_on_evict_fires_on_lru_eviction() -> None:
    """Filling past the cap evicts a still-live pending consent; the callback
    must receive its full key so the caller can fail-close it."""
    evicted: list[ConsentKey] = []
    gate = TaskConsentGate(maxsize=2, on_evict=evicted.append)
    gate.open(_T1, "field-a")
    gate.open(_T2, "field-a")
    gate.open(_T3, "field-a")  # Evicts oldest live entry.
    assert evicted == [("srv-a", "task-1", "field-a")]


def test_on_evict_fires_on_lazy_ttl_expiry() -> None:
    """A pending consent found expired on access is evicted and fires the callback."""
    evicted: list[ConsentKey] = []
    gate = TaskConsentGate(ttl=0.0, on_evict=evicted.append)
    gate.open(_T1, "field-a")
    assert gate.is_consent_pending(_T1, "field-a") is False  # Expired -> evict.
    assert evicted == [("srv-a", "task-1", "field-a")]


def test_on_evict_fires_on_lazy_ttl_expiry_via_answer() -> None:
    """An expired consent hit by ``answer`` is evicted, fires the callback, and rejects."""
    evicted: list[ConsentKey] = []
    gate = TaskConsentGate(ttl=0.0, on_evict=evicted.append)
    gate.open(_T1, "field-a")
    assert gate.answer(_T1, "field-a") is False  # Expired -> reject + evict.
    assert evicted == [("srv-a", "task-1", "field-a")]


def test_discard_does_not_fire_on_evict() -> None:
    evicted: list[ConsentKey] = []
    gate = TaskConsentGate(on_evict=evicted.append)
    gate.open(_T1, "field-a")
    gate.open(_T1, "field-b")
    gate.discard(_T1)
    assert evicted == []  # Deliberate terminal removal: no eviction callback.


def test_clear_does_not_fire_on_evict() -> None:
    evicted: list[ConsentKey] = []
    gate = TaskConsentGate(on_evict=evicted.append)
    gate.open(_T1, "field-a")
    gate.open(_T2, "field-b")
    gate.clear()
    assert evicted == []  # Deliberate terminal removal: no eviction callback.


def test_on_evict_failure_does_not_break_gate() -> None:
    def boom(_key: ConsentKey) -> None:
        raise RuntimeError("ledger down")

    gate = TaskConsentGate(maxsize=1, on_evict=boom)
    gate.open(_T1, "field-a")
    gate.open(_T2, "field-a")  # Evicts _T1; callback raises but is swallowed.
    assert gate.is_consent_pending(_T2, "field-a") is True
