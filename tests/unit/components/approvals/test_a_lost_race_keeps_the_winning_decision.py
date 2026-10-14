"""A decision that lands first is the one the record and the held call keep (#1794).

Every transition of an approval request is conditional on it still being
pending. The gate and the resolver used to write unconditionally, so a decision
that landed while another actor was about to write was overwritten, and the
event the gate published disagreed with the record.

The repository here applies a competing write at the moment of the loser's
transition, exactly the interleaving two replicas sharing a store produce.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest, ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class _Repository:
    """In-memory, with a competing decision that lands just before a chosen transition."""

    def __init__(self) -> None:
        self.store: dict[str, ApprovalRequest] = {}
        self.rival: tuple[ApprovalState, str] | None = None

    async def save(self, request: ApprovalRequest) -> None:
        self.store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.store.get(approval_id)

    async def update_state(self, approval_id, state, decided_by, decided_at, reason) -> None:
        raise AssertionError("an unconditional write: nothing may overwrite a decision (#1794)")

    async def transition(self, approval_id, from_states, state, decided_by, decided_at, reason) -> bool:
        if self.rival is not None:
            rival_state, rival_by = self.rival
            self.rival = None
            row = self.store[approval_id]
            row.state, row.decided_by, row.decided_at = rival_state, rival_by, datetime.now(UTC)
        row = self.store.get(approval_id)
        if row is None or row.state not in from_states:
            return False
        row.state, row.decided_by, row.decided_at, row.reason = state, decided_by, decided_at, reason
        return True


class _Delivery:
    async def send(self, request: ApprovalRequest) -> None:
        return None


def _service() -> tuple[ApprovalGateService, _Repository, list[str]]:
    repository = _Repository()
    bus = MagicMock()
    events: list[str] = []
    bus.publish = lambda event: events.append(type(event).__name__)
    service = ApprovalGateService(
        repository=repository, hold_registry=ApprovalHoldRegistry(), event_bus=bus, delivery=_Delivery()
    )
    return service, repository, events


POLICY = ToolAccessPolicy(approval_list=("refund_payment",), approval_timeout_seconds=0)


async def test_an_approval_that_lands_as_the_deadline_passes_is_granted_not_overwritten() -> None:
    service, repository, events = _service()
    repository.rival = (ApprovalState.APPROVED, "alice")

    # The hold times out at once; the approval lands before the gate's own expiry write.
    result = await service.check(
        mcp_server_id="payments", tool_name="refund_payment", arguments={}, policy=POLICY, correlation_id="c1"
    )

    (record,) = repository.store.values()
    assert record.state is ApprovalState.APPROVED, "the expiry overwrote a decision that landed first"
    assert result.approved is True
    assert "ToolApprovalExpired" not in events
    assert events.count("ToolApprovalGranted") == 1


async def test_a_denial_that_lands_as_the_deadline_passes_is_denied() -> None:
    service, repository, events = _service()
    repository.rival = (ApprovalState.DENIED, "bob")

    result = await service.check(
        mcp_server_id="payments", tool_name="refund_payment", arguments={}, policy=POLICY, correlation_id="c2"
    )

    (record,) = repository.store.values()
    assert record.state is ApprovalState.DENIED
    assert result.approved is False and result.error_code == "approval_denied"
    assert events.count("ToolApprovalDenied") == 1 and "ToolApprovalExpired" not in events


async def test_a_request_nobody_decides_expires_once() -> None:
    service, repository, events = _service()

    result = await service.check(
        mcp_server_id="payments", tool_name="refund_payment", arguments={}, policy=POLICY, correlation_id="c3"
    )

    (record,) = repository.store.values()
    assert record.state is ApprovalState.EXPIRED
    assert result.error_code == "approval_timeout"
    assert events.count("ToolApprovalExpired") == 1


async def test_a_resolver_that_loses_the_race_changes_nothing_and_says_so() -> None:
    service, repository, _events = _service()
    request = ApprovalRequest(
        approval_id="a-1",
        mcp_server_id="payments",
        tool_name="refund_payment",
        arguments={},
        arguments_hash="0" * 64,
        requested_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        state=ApprovalState.PENDING,
        channel="event_stream",
    )
    await repository.save(request)
    repository.rival = (ApprovalState.DENIED, "bob")

    won = await service.resolve("a-1", True, "alice")

    assert won is False
    record = await repository.get("a-1")
    assert record is not None
    assert record.state is ApprovalState.DENIED and record.decided_by == "bob"
