"""Unit tests for approval gate models."""

from datetime import datetime, timedelta, timezone

import pytest

from enterprise.approvals.models import ApprovalRequest, ApprovalResult, ApprovalState


def _make_request(**overrides) -> ApprovalRequest:
    now = datetime.now(timezone.utc)
    defaults = dict(
        approval_id="test-id-001",
        provider_id="notion",
        tool_name="update_page",
        arguments={"page_id": "abc"},
        arguments_hash="sha256:abc123",
        requested_at=now,
        expires_at=now + timedelta(seconds=300),
        state=ApprovalState.PENDING,
        channel="dashboard",
    )
    defaults.update(overrides)
    return ApprovalRequest(**defaults)


class TestApprovalRequestStateMachine:
    """Valid transitions: PENDING -> APPROVED | DENIED | EXPIRED."""

    def test_approve_from_pending(self):
        req = _make_request()
        req.approve("admin@example.com")
        assert req.state == ApprovalState.APPROVED
        assert req.decided_by == "admin@example.com"
        assert req.decided_at is not None

    def test_deny_from_pending(self):
        req = _make_request()
        req.deny("admin@example.com", reason="Not safe")
        assert req.state == ApprovalState.DENIED
        assert req.reason == "Not safe"

    def test_expire_from_pending(self):
        req = _make_request()
        req.expire()
        assert req.state == ApprovalState.EXPIRED
        assert req.decided_at is not None

    def test_approve_on_approved_raises(self):
        req = _make_request(state=ApprovalState.APPROVED)
        with pytest.raises(ValueError, match="Cannot approve"):
            req.approve("admin@example.com")

    def test_deny_on_approved_raises(self):
        req = _make_request(state=ApprovalState.APPROVED)
        with pytest.raises(ValueError, match="Cannot deny"):
            req.deny("admin@example.com")

    def test_approve_on_denied_raises(self):
        req = _make_request(state=ApprovalState.DENIED)
        with pytest.raises(ValueError, match="Cannot approve"):
            req.approve("admin@example.com")

    def test_deny_on_denied_raises(self):
        req = _make_request(state=ApprovalState.DENIED)
        with pytest.raises(ValueError, match="Cannot deny"):
            req.deny("admin@example.com")

    def test_approve_on_expired_raises(self):
        req = _make_request(state=ApprovalState.EXPIRED)
        with pytest.raises(ValueError, match="Cannot approve"):
            req.approve("admin@example.com")

    def test_expire_on_non_pending_is_idempotent(self):
        req = _make_request(state=ApprovalState.APPROVED)
        req.expire()
        assert req.state == ApprovalState.APPROVED

    def test_is_terminal(self):
        assert _make_request(state=ApprovalState.PENDING).is_terminal() is False
        assert _make_request(state=ApprovalState.APPROVED).is_terminal() is True
        assert _make_request(state=ApprovalState.DENIED).is_terminal() is True
        assert _make_request(state=ApprovalState.EXPIRED).is_terminal() is True


class TestApprovalResult:
    """Factory methods and frozen semantics."""

    def test_not_required(self):
        result = ApprovalResult.not_required()
        assert result.approved is True
        assert result.approval_id is None
        assert result.error_code is None

    def test_granted(self):
        result = ApprovalResult.granted("id-001")
        assert result.approved is True
        assert result.approval_id == "id-001"

    def test_denied(self):
        result = ApprovalResult.denied("id-001", reason="Too risky")
        assert result.approved is False
        assert result.error_code == "approval_denied"
        assert result.reason == "Too risky"

    def test_expired(self):
        result = ApprovalResult.expired("id-001")
        assert result.approved is False
        assert result.error_code == "approval_timeout"

    def test_frozen(self):
        result = ApprovalResult.not_required()
        with pytest.raises(AttributeError):
            result.approved = False  # type: ignore
