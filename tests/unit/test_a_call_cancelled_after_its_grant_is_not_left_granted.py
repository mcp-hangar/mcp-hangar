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
