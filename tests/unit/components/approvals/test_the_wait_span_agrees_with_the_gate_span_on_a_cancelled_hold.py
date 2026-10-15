"""A refused late approval reads cancelled on both spans (#1731).

The hold is woken with a refusal when an approval arrives for a call whose
caller gave up, so `approval_gate.wait_for_decision` read `denied` while
`approval_gate.flow` read `cancelled`. The trace contradicted itself.
"""

from __future__ import annotations

import asyncio
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy

POLICY = ToolAccessPolicy(approval_list=("refund_payment",), approval_timeout_seconds=5)


class _Repository:
    def __init__(self) -> None:
        self.store: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self.store[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self.store.get(approval_id)

    async def transition(self, approval_id, from_states, state, decided_by, decided_at, reason) -> bool:
        row = self.store.get(approval_id)
        if row is None or row.state not in from_states:
            return False
        row.state, row.decided_by, row.decided_at, row.reason = state, decided_by, decided_at, reason
        return True


class _Delivery:
    async def send(self, request: ApprovalRequest) -> None:
        return None


class _Spans:
    """A tracer double: span name -> the attributes set on it."""

    def __init__(self) -> None:
        self.attributes: dict[str, dict[str, object]] = {}

    @contextmanager
    def start_as_current_span(self, name: str, *args, **kwargs):
        span = MagicMock()
        attrs = self.attributes.setdefault(name, {})
        span.set_attribute = lambda key, value: attrs.__setitem__(key, value)
        yield span


async def _run(answer: bool) -> dict[str, dict[str, object]]:
    repository = _Repository()
    service = ApprovalGateService(
        repository=repository, hold_registry=ApprovalHoldRegistry(), event_bus=MagicMock(), delivery=_Delivery()
    )
    abandoned = threading.Event()
    spans = _Spans()
    with patch("mcp_hangar.approvals.service.get_tracer", return_value=spans):
        held = asyncio.create_task(
            service.check(
                mcp_server_id="payments",
                tool_name="refund_payment",
                arguments={},
                policy=POLICY,
                correlation_id="c1",
                abandoned=abandoned,
            )
        )
        while not repository.store:
            await asyncio.sleep(0.01)
        (approval_id,) = repository.store
        await asyncio.sleep(0.05)  # the hold is registered after the record is saved
        abandoned.set()
        await service.resolve(approval_id, answer, "alice", None if answer else "no")
        await asyncio.wait_for(held, timeout=5)
    return spans.attributes


async def test_a_refused_late_approval_reads_cancelled_on_both_spans() -> None:
    spans = await _run(answer=True)

    assert spans["approval_gate.wait_for_decision"]["approval.decision"] == "cancelled"
    assert spans["approval_gate.flow"]["approval.result"] == "cancelled"


async def test_a_denial_of_an_abandoned_call_still_reads_denied() -> None:
    spans = await _run(answer=False)

    assert spans["approval_gate.wait_for_decision"]["approval.decision"] == "denied"
