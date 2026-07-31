"""An approval decided at T is not automatically an approval usable at T+N.

The gate blocks for up to ``approval_timeout_seconds`` (300 by default) and
everything it rested on -- policy, tool withdrawal, the pinned digest, the
arguments themselves -- was checked before that pause and nothing after it.
These cover the re-check that closes the gap, plus the two fields that looked
like bindings and enforced nothing: ``expires_at`` and ``arguments_hash``.
"""

from datetime import UTC, datetime, timedelta

import pytest

from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService, _hash_arguments, _sanitize_arguments

from .test_approval_gate_service import FakeRepository


def _service(repo):
    return ApprovalGateService(
        repository=repo,
        hold_registry=None,
        event_bus=None,
        delivery=None,
    )


def _approved(arguments, *, expires_in=300, state=ApprovalState.APPROVED):
    sanitized = _sanitize_arguments(arguments)
    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id="ap-1",
        provider_id="srv",
        tool_name="transfer",
        arguments=sanitized,
        arguments_hash=_hash_arguments(sanitized),
        requested_at=now,
        expires_at=now + timedelta(seconds=expires_in),
        state=state,
        channel="dashboard",
        decided_by="alice",
        requested_by="agent-7",
    )


class TestRevalidateAtDispatch:
    @pytest.mark.asyncio
    async def test_unchanged_approval_still_holds(self):
        repo = FakeRepository()
        args = {"amount": 10, "to": "acct-2"}
        await repo.save(_approved(args))

        assert await _service(repo).revalidate("ap-1", args) is None

    @pytest.mark.asyncio
    async def test_arguments_changed_after_approval_is_refused(self):
        """The request mutator pipeline runs *after* the gate.

        Nothing compared the dispatched payload against the approved one, so a
        registered mutator could rewrite the arguments a human had just agreed
        to and the call would go out unremarked.
        """
        repo = FakeRepository()
        await repo.save(_approved({"amount": 10, "to": "acct-2"}))

        reason = await _service(repo).revalidate("ap-1", {"amount": 10_000, "to": "acct-2"})

        assert reason == "arguments changed after approval"

    @pytest.mark.asyncio
    async def test_redacted_keys_do_not_cause_false_refusals(self):
        """The stored hash is over *sanitized* arguments.

        Comparing a raw payload against it would refuse every call carrying a
        secret-shaped key -- a fix that fails closed on healthy traffic is not
        a fix.
        """
        repo = FakeRepository()
        args = {"amount": 10, "api_key": "sk-live-1234"}
        await repo.save(_approved(args))

        assert await _service(repo).revalidate("ap-1", args) is None

    @pytest.mark.asyncio
    async def test_expiry_during_the_hold_is_refused(self):
        repo = FakeRepository()
        await repo.save(_approved({"amount": 10}, expires_in=-1))

        reason = await _service(repo).revalidate("ap-1", {"amount": 10})

        assert reason == "approval expired during the hold"

    @pytest.mark.asyncio
    async def test_non_approved_state_is_refused(self):
        repo = FakeRepository()
        await repo.save(_approved({"amount": 10}, state=ApprovalState.DENIED))

        reason = await _service(repo).revalidate("ap-1", {"amount": 10})

        assert reason is not None
        assert "denied" in reason

    @pytest.mark.asyncio
    async def test_vanished_record_is_refused_not_allowed(self):
        """Fail closed: an approval we cannot read is not an approval."""
        reason = await _service(FakeRepository()).revalidate("ap-missing", {})

        assert reason == "approval record is gone"


class TestExpiryPredicate:
    def test_expires_at_is_actually_consulted(self):
        past = _approved({}, expires_in=-1)
        future = _approved({}, expires_in=300)

        assert past.is_expired() is True
        assert future.is_expired() is False

    def test_requester_is_recorded_alongside_the_decider(self):
        """The record named who decided and never who asked."""
        request = _approved({})

        assert request.requested_by == "agent-7"
        assert request.decided_by == "alice"
