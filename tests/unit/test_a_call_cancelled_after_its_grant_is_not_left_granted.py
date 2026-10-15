"""A call cancelled after its approval was granted does not read as an effective grant (#1730).

The gate publishes `ToolApprovalGranted` when the hold ends. If the batch's
cancel lands after that -- during the re-checks that follow the hold, or the
cold start -- the call is refused as "Cancelled after cold start" and never
runs, yet the trail said granted (the shape #1702 fixed for a cancel during the
hold). The record and the trail now say cancelled as well.

The cancel is injected at the cold start, the gate right after the approval
gate; the executor, the gate service, the repository and the event bus are the
production ones, as in `test_a_held_call_whose_batch_deadline_passes.py`.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.service import CANCELLED_AFTER_GRANT_REASON, ApprovalGateService
from mcp_hangar.domain.events import ToolApprovalCancelled, ToolApprovalGranted
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch import executor as executor_mod
from tests.unit.test_an_approval_hold_leaves_nothing_behind import SERVER, TOOL, _hold_for, served_world


def _published(service: ApprovalGateService) -> list[Any]:
    return [c.args[0] for c in service._event_bus.publish.call_args_list]  # type: ignore[attr-defined]


def _run_batch() -> Any:
    return (
        BatchExecutor()
        .execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={"amount": 10})],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )
        .results[0]
    )


def _gates_cancelling_at_the_cold_start() -> tuple[Any, ...]:
    def _gate_cold_start(executor: BatchExecutor, p: Any) -> Any:
        refusal = BatchExecutor._gate_cold_start(executor, p)
        p.cancel_event.set()  # the batch is cancelled while the server starts
        return refusal

    return tuple(_gate_cold_start if g is BatchExecutor._gate_cold_start else g for g in executor_mod._GATES)


def test_a_cancel_after_the_grant_records_the_approval_cancelled() -> None:
    with served_world() as world:
        world.approver.answer = True
        _hold_for(30)
        with patch.object(executor_mod, "_GATES", _gates_cancelling_at_the_cold_start()):
            result = _run_batch()
        service = world.approver.service
        assert service is not None
        (approval_id,) = world.approver.approval_ids
        record = asyncio.run(service._repository.get(approval_id))
        events = _published(service)

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    # The grant was real and was published; the trail then says the call did not run.
    kinds = [type(e) for e in events if isinstance(e, (ToolApprovalGranted, ToolApprovalCancelled))]
    assert kinds == [ToolApprovalGranted, ToolApprovalCancelled], events
    (cancelled,) = [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert cancelled.approval_id == approval_id
    assert cancelled.attempted_by == "approver-1"
    assert cancelled.reason == CANCELLED_AFTER_GRANT_REASON
    assert record.state is ApprovalState.CANCELLED


def test_an_approval_nobody_cancels_stays_granted() -> None:
    with served_world() as world:
        world.approver.answer = True
        _hold_for(30)
        result = _run_batch()
        service = world.approver.service
        assert service is not None
        (approval_id,) = world.approver.approval_ids
        record = asyncio.run(service._repository.get(approval_id))
        events = _published(service)

    assert result.success, result
    assert [e for e in events if isinstance(e, ToolApprovalGranted)]
    assert not [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert record.state is ApprovalState.APPROVED


def test_a_cancel_with_no_approval_in_play_records_nothing() -> None:
    """A call that needed no approval has none to cancel."""
    with served_world() as world:
        service = world.approver.service
        with patch.object(executor_mod, "_GATES", _gates_cancelling_at_the_cold_start()):
            result = _run_batch()
        events = _published(service) if service is not None else []

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    assert not [e for e in events if isinstance(e, ToolApprovalCancelled)]


# --- the service method, branch by branch ------------------------------------------


def _service_with(state: ApprovalState | None) -> tuple[ApprovalGateService, _Store, list[Any]]:
    from datetime import UTC, datetime, timedelta
    from unittest.mock import MagicMock

    from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
    from mcp_hangar.approvals.models import ApprovalRequest

    store = _Store()
    if state is not None:
        now = datetime.now(UTC)
        store.rows["a-1"] = ApprovalRequest(
            approval_id="a-1",
            provider_id="payments",
            tool_name="refund",
            arguments={},
            arguments_hash="h",
            requested_at=now,
            expires_at=now + timedelta(minutes=5),
            state=state,
            channel="test",
            decided_by="alice",
        )
    published: list[Any] = []
    bus = MagicMock()
    bus.publish = published.append
    service = ApprovalGateService(
        repository=store, hold_registry=ApprovalHoldRegistry(), event_bus=bus, delivery=MagicMock()
    )
    return service, store, published


class _Store:
    def __init__(self) -> None:
        self.rows: dict[str, Any] = {}
        self.lose_the_race = False

    async def get(self, approval_id: str) -> Any:
        return self.rows.get(approval_id)

    async def transition(self, approval_id, from_states, state, decided_by, decided_at, reason) -> bool:
        if self.lose_the_race:
            return False
        row = self.rows[approval_id]
        if row.state not in from_states:
            return False
        row.state, row.decided_by, row.reason = state, decided_by, reason
        return True


async def test_cancel_after_grant_moves_an_approved_record_and_publishes() -> None:
    service, store, published = _service_with(ApprovalState.APPROVED)

    assert await service.cancel_after_grant("a-1") is True

    assert store.rows["a-1"].state is ApprovalState.CANCELLED
    (event,) = published
    assert isinstance(event, ToolApprovalCancelled)
    assert (event.mcp_server_id, event.tool_name, event.attempted_by) == ("payments", "refund", "alice")


async def test_cancel_after_grant_has_nothing_to_do_for_a_missing_record() -> None:
    service, _store, published = _service_with(None)

    assert await service.cancel_after_grant("a-1") is False
    assert published == []


@pytest.mark.parametrize("state", [ApprovalState.PENDING, ApprovalState.DENIED, ApprovalState.CANCELLED])
async def test_cancel_after_grant_leaves_a_record_that_is_not_approved(state) -> None:
    service, store, published = _service_with(state)

    assert await service.cancel_after_grant("a-1") is False
    assert store.rows["a-1"].state is state and published == []


async def test_cancel_after_grant_yields_to_a_decision_that_landed_first() -> None:
    service, store, published = _service_with(ApprovalState.APPROVED)
    store.lose_the_race = True

    assert await service.cancel_after_grant("a-1") is False
    assert store.rows["a-1"].state is ApprovalState.APPROVED and published == []


# --- the executor's side -----------------------------------------------------------


def _pipeline(gate_service: Any, approval_id: str | None = "a-1") -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(granted_approval_id=approval_id, ctx=SimpleNamespace(approval_gate=gate_service))


def test_a_failure_to_record_the_cancel_is_logged_and_does_not_raise() -> None:
    class Broken:
        async def cancel_after_grant(self, approval_id: str) -> bool:
            raise OSError("store down")

    BatchExecutor._cancel_granted_approval(_pipeline(Broken()))  # must not raise


def test_a_gate_without_the_method_and_a_call_without_a_grant_are_left_alone() -> None:
    calls: list[str] = []

    class Recorder:
        async def cancel_after_grant(self, approval_id: str) -> bool:
            calls.append(approval_id)
            return True

    BatchExecutor._cancel_granted_approval(_pipeline(object()))
    BatchExecutor._cancel_granted_approval(_pipeline(None))
    BatchExecutor._cancel_granted_approval(_pipeline(Recorder(), approval_id=None))
    assert calls == []

    BatchExecutor._cancel_granted_approval(_pipeline(Recorder()))
    assert calls == ["a-1"]
