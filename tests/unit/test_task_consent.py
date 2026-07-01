"""Unit tests for the fail-closed :class:`TaskConsentGate`."""

from __future__ import annotations

import pytest

from mcp_hangar.domain.services.task_consent import TaskConsentGate


def test_open_then_answer_matches() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    assert gate.answer("task-1", "field-a") is True


def test_answer_clears_pending_consent() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    assert gate.is_consent_pending("task-1", "field-a") is True
    assert gate.answer("task-1", "field-a") is True
    # Consent was consumed by the answer.
    assert gate.is_consent_pending("task-1", "field-a") is False


def test_second_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    assert gate.answer("task-1", "field-a") is True
    # A replayed answer has no pending consent to match.
    assert gate.answer("task-1", "field-a") is False


def test_answer_with_no_pending_fail_closed() -> None:
    gate = TaskConsentGate()
    # No consent was ever opened: an injected answer is rejected.
    assert gate.answer("task-1", "field-a") is False


def test_unknown_task_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    assert gate.answer("task-2", "field-a") is False


def test_unknown_input_key_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    assert gate.answer("task-1", "field-b") is False


def test_empty_task_id_open_raises() -> None:
    gate = TaskConsentGate()
    with pytest.raises(ValueError):
        gate.open("", "field-a")


def test_empty_input_key_open_raises() -> None:
    gate = TaskConsentGate()
    with pytest.raises(ValueError):
        gate.open("task-1", "")


def test_empty_args_answer_fail_closed() -> None:
    gate = TaskConsentGate()
    assert gate.answer("", "field-a") is False
    assert gate.answer("task-1", "") is False


def test_empty_args_is_consent_pending_fail_closed() -> None:
    gate = TaskConsentGate()
    assert gate.is_consent_pending("", "field-a") is False
    assert gate.is_consent_pending("task-1", "") is False


def test_is_consent_pending_transitions() -> None:
    gate = TaskConsentGate()
    assert gate.is_consent_pending("task-1", "field-a") is False
    gate.open("task-1", "field-a")
    assert gate.is_consent_pending("task-1", "field-a") is True
    _ = gate.answer("task-1", "field-a")
    assert gate.is_consent_pending("task-1", "field-a") is False


def test_discard_removes_all_task_inputs() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    gate.open("task-1", "field-b")
    gate.open("task-2", "field-a")
    gate.discard("task-1")
    assert gate.is_consent_pending("task-1", "field-a") is False
    assert gate.is_consent_pending("task-1", "field-b") is False
    # Other tasks are untouched.
    assert gate.is_consent_pending("task-2", "field-a") is True


def test_clear_empties_gate() -> None:
    gate = TaskConsentGate()
    gate.open("task-1", "field-a")
    gate.open("task-2", "field-b")
    gate.clear()
    assert gate.is_consent_pending("task-1", "field-a") is False
    assert gate.is_consent_pending("task-2", "field-b") is False


def test_ttl_expiry_makes_consent_unanswerable() -> None:
    # Zero TTL: any elapsed time expires the pending consent, so the answer
    # is rejected fail-closed.
    gate = TaskConsentGate(ttl=0.0)
    gate.open("task-1", "field-a")
    assert gate.answer("task-1", "field-a") is False


def test_ttl_expiry_makes_consent_not_pending() -> None:
    gate = TaskConsentGate(ttl=0.0)
    gate.open("task-1", "field-a")
    assert gate.is_consent_pending("task-1", "field-a") is False
