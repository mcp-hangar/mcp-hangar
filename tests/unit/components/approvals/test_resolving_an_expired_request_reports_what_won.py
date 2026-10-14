"""Resolving a request whose window has passed reports the state that actually won (#1794).

The resolve command marks an approval whose ``expires_at`` has passed as
expired before refusing the decision. That write is now conditional on the
request still being pending, so when another actor moved it first -- a resolver
on another replica, the gate's own expiry, a cancel -- the caller is told what
now exists and not an expiry that did not happen.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mcp_hangar.approvals.commands.resolve import ResolveApprovalCommand, ResolveApprovalHandler, ResolveOutcome
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.security import Principal, PrincipalId, PrincipalType

from .test_approval_gate_service import FakeRepository


class _RivalRepository(FakeRepository):
    """Applies a competing write (or deletes the row) at the instant of the first transition."""

    def __init__(self, rival: ApprovalState | None, *, delete: bool = False) -> None:
        super().__init__()
        self._rival, self._delete = rival, delete

    async def transition(self, approval_id, from_states, state, decided_by, decided_at, reason):
        if self._delete:
            self._store.pop(approval_id, None)
            self._delete = False
        elif self._rival is not None:
            self._store[approval_id].state = self._rival
            self._rival = None
        return await super().transition(approval_id, from_states, state, decided_by, decided_at, reason)


def _expired_request() -> ApprovalRequest:
    now = datetime.now(UTC)
    return ApprovalRequest(
        approval_id="ap-1",
        provider_id="bank",
        tool_name="wire_transfer",
        arguments={},
        arguments_hash="h",
        requested_at=now - timedelta(minutes=10),
        expires_at=now - timedelta(minutes=5),
        state=ApprovalState.PENDING,
        channel="dashboard",
    )


async def _resolve(repository: FakeRepository):
    await repository.save(_expired_request())
    service = ApprovalGateService(
        repository=repository, hold_registry=ApprovalHoldRegistry(), event_bus=None, delivery=None
    )
    principal = Principal(id=PrincipalId("approver"), type=PrincipalType.USER, tenant_id=None)
    return await ResolveApprovalHandler(service, auth_components=None).handle(
        ResolveApprovalCommand(approval_id="ap-1", approved=True, principal=principal)
    )


@pytest.mark.asyncio
async def test_an_expired_request_nobody_moved_is_expired() -> None:
    repository = _RivalRepository(None)

    result = await _resolve(repository)

    assert result.outcome is ResolveOutcome.EXPIRED
    assert (await repository.get("ap-1")).state is ApprovalState.EXPIRED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("rival", "outcome"),
    [
        (ApprovalState.APPROVED, ResolveOutcome.ALREADY_TERMINAL),
        (ApprovalState.DENIED, ResolveOutcome.ALREADY_TERMINAL),
        (ApprovalState.CANCELLED, ResolveOutcome.CANCELLED),
        (ApprovalState.EXPIRED, ResolveOutcome.EXPIRED),
    ],
)
async def test_a_request_someone_moved_first_reports_the_state_that_won(rival, outcome) -> None:
    repository = _RivalRepository(rival)

    result = await _resolve(repository)

    assert result.outcome is outcome
    assert result.state == rival.value
    assert (await repository.get("ap-1")).state is rival, "the loser overwrote the winner"


@pytest.mark.asyncio
async def test_a_request_deleted_in_between_is_not_found() -> None:
    result = await _resolve(_RivalRepository(None, delete=True))

    assert result.outcome is ResolveOutcome.NOT_FOUND
