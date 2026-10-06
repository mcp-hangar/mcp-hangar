"""Which deadline names a held call when its batch's deadline passes too (#1541).

The rule: the gate that stopped the call names it.

- The batch budget is spent before the call reaches the approval gate: the
  budget gate refuses it, `TimeoutError`, and nobody is asked to approve.
- The call is already held when the batch deadline passes: the hold is not
  cut short. The hold's own outcome names the call -- `approval_timeout` if
  nobody answers, `approval_denied` if the approver says no. An approval that
  arrives after the deadline does not dispatch the call: it reads
  `CancellationError`, and the approval is refused and recorded `cancelled`,
  never granted (#1702) -- the audit trail must not say someone let through a
  call that did not run.

A consequence a caller should know: the batch deadline does not bound how long
a batch with a held call takes to return. The collector waits for the held
worker, so the batch returns when the hold ends.

The deadline is made to pass while the call is held without a clock: the
collector's `as_completed` wait first waits until the call is held, then times
out at once -- the collector's own `TimeoutError` branch, as a spent deadline
takes it. An answer "after the deadline" is sent from the cancellation counter
the collector bumps right after it sets the batch's cancel event.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from concurrent.futures import as_completed
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock, patch

from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar.approvals import service as service_mod
from mcp_hangar.approvals.api.routes import approval_routes
from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.events import ToolApprovalCancelled, ToolApprovalGranted
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from mcp_hangar.server.tools.batch import executor as executor_mod
from mcp_hangar.server.tools.batch.executor import BatchResult
from tests.unit.test_an_approval_hold_leaves_nothing_behind import SERVER, TOOL, World, _hold_for, served_world

_EXECUTOR = "mcp_hangar.server.tools.batch.executor"


def _batch(global_timeout: float) -> BatchResult:
    return BatchExecutor().execute(
        batch_id="b",
        calls=[CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={"amount": 10})],
        max_concurrency=1,
        global_timeout=global_timeout,
        fail_fast=False,
    )


def _deadline_while_held(
    world: World, answer: bool | None, answer_with: Callable[[ApprovalGateService, str], None] | None = None
) -> BatchResult:
    """Run one batch whose deadline passes while its call is held, then answer `answer` (None: never).

    `answer_with`, when given, answers instead -- over another surface than the service.
    """
    world.approver.answer = None  # the double never answers on its own here
    held_at_deadline: list[bool] = []

    def deadline_after_the_hold(fs: Any, timeout: float | None = None) -> Iterator[Any]:
        held_at_deadline.append(world.approver.wait_until_held(1, timeout=10))
        return as_completed(fs, timeout=0)

    def answer_after_the_deadline(**_: Any) -> None:
        service = world.approver.service
        assert service is not None
        if answer_with is not None:
            (approval_id,) = world.approver.approval_ids
            answer_with(service, approval_id)
            return
        if answer is None:
            return
        (approval_id,) = world.approver.approval_ids
        asyncio.run_coroutine_threadsafe(
            service.resolve(approval_id, answer, "approver-1", None if answer else "no"), world.approver._loop
        ).result(timeout=10)

    cancellations = Mock()
    cancellations.inc.side_effect = answer_after_the_deadline
    with (
        patch(f"{_EXECUTOR}.as_completed", deadline_after_the_hold),
        patch(f"{_EXECUTOR}.BATCH_CANCELLATIONS_TOTAL", cancellations),
    ):
        batch = _batch(global_timeout=30.0)

    assert held_at_deadline == [True], "the deadline passed before the call was held"
    cancellations.inc.assert_called_once_with(reason="timeout")
    return batch


def test_a_budget_spent_before_the_hold_reads_batch_timeout_and_nobody_is_asked() -> None:
    with served_world() as world:
        _hold_for(30)
        result = _batch(global_timeout=-1).results[0]

    assert (result.success, result.error_type) == (False, "TimeoutError"), result
    assert world.approver.held == 0


def test_a_held_call_nobody_answers_reads_approval_timeout_after_the_deadline() -> None:
    with served_world() as world:
        _hold_for(1)
        batch = _deadline_while_held(world, answer=None)

    result = batch.results[0]
    assert (result.success, result.error_type) == (False, "approval_timeout"), result
    # The deadline did not cut the hold short: the batch returned when the hold ended.
    assert batch.elapsed_ms >= 1000, batch.elapsed_ms


def test_a_held_call_denied_after_the_deadline_reads_approval_denied() -> None:
    with served_world() as world:
        _hold_for(30)
        result = _deadline_while_held(world, answer=False).results[0]

    assert (result.success, result.error_type) == (False, "approval_denied"), result


def test_a_held_call_approved_after_the_deadline_is_not_dispatched() -> None:
    with served_world() as world:
        context = executor_mod.get_context()
        _hold_for(30)
        result = _deadline_while_held(world, answer=True).results[0]

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    context.command_bus.send.assert_not_called()


# --- the approval is recorded cancelled, never granted (#1702) ------------------


def _published(service: ApprovalGateService) -> list[Any]:
    return [c.args[0] for c in service._event_bus.publish.call_args_list]  # type: ignore[attr-defined]


def _record(service: ApprovalGateService, approval_id: str) -> Any:
    return asyncio.run(service._repository.get(approval_id))


def test_an_approval_after_the_deadline_is_refused_and_recorded_cancelled() -> None:
    refused: list[bool] = []

    def approve(service: ApprovalGateService, approval_id: str) -> None:
        refused.append(not asyncio.run(service.resolve(approval_id, True, "approver-1")))

    with served_world() as world:
        _hold_for(30)
        result = _deadline_while_held(world, answer=None, answer_with=approve).results[0]
        service = world.approver.service
        assert service is not None
        (approval_id,) = world.approver.approval_ids

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    assert refused == [True]
    events = _published(service)
    assert not [e for e in events if isinstance(e, ToolApprovalGranted)], events
    (cancelled,) = [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert cancelled.attempted_by == "approver-1"
    assert _record(service, approval_id).state is ApprovalState.CANCELLED


def test_an_approval_after_the_deadline_over_rest_answers_409() -> None:
    responses: list[Any] = []

    def approve_over_rest(service: ApprovalGateService, approval_id: str) -> None:
        app = Starlette(routes=[Mount("/api", routes=approval_routes)])
        app.state.approval_gate_service = service
        with TestClient(app) as client:
            responses.append(client.post(f"/api/approvals/{approval_id}/resolve", json={"decision": "approve"}))
            # A second try reads the same refusal from the record.
            responses.append(client.post(f"/api/approvals/{approval_id}/resolve", json={"decision": "approve"}))

    with served_world() as world:
        context = executor_mod.get_context()
        _hold_for(30)
        result = _deadline_while_held(world, answer=None, answer_with=approve_over_rest).results[0]
        service = world.approver.service
        assert service is not None

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    assert [r.status_code for r in responses] == [409, 409], [r.text for r in responses]
    for response in responses:
        body = response.json()
        assert body["state"] == "cancelled"
        assert "cancelled" in body["error"]
    assert not [e for e in _published(service) if isinstance(e, ToolApprovalGranted)]
    context.command_bus.send.assert_not_called()


def test_an_approval_recorded_elsewhere_after_the_deadline_is_not_granted() -> None:
    """The approver reached another instance, which has no hold to see the cancel: the record says approved.

    The instance holding the call reads the decision from the record, sees its
    batch was cancelled, and records the approval cancelled instead of granted.
    """

    def approve_elsewhere(service: ApprovalGateService, approval_id: str) -> None:
        asyncio.run(
            service._repository.update_state(
                approval_id, ApprovalState.APPROVED, "approver-elsewhere", datetime.now(UTC), None
            )
        )

    with served_world() as world, patch.object(service_mod, "SHARED_POLL_INTERVAL_S", 0.05):
        _hold_for(30)
        result = _deadline_while_held(world, answer=None, answer_with=approve_elsewhere).results[0]
        service = world.approver.service
        assert service is not None
        (approval_id,) = world.approver.approval_ids

    assert (result.success, result.error_type) == (False, "CancellationError"), result
    events = _published(service)
    assert not [e for e in events if isinstance(e, ToolApprovalGranted)], events
    (cancelled,) = [e for e in events if isinstance(e, ToolApprovalCancelled)]
    assert cancelled.attempted_by == "approver-elsewhere"
    assert _record(service, approval_id).state is ApprovalState.CANCELLED


def test_an_approval_before_the_deadline_is_still_granted() -> None:
    """The refusal is for an abandoned call only: an approval in time is a grant, as before."""
    with served_world() as world:
        world.approver.answer = True
        _hold_for(30)
        result = (
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
        service = world.approver.service
        assert service is not None

    assert result.success, result
    events = _published(service)
    assert [e for e in events if isinstance(e, ToolApprovalGranted)], events
    assert not [e for e in events if isinstance(e, ToolApprovalCancelled)], events
