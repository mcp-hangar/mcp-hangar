"""Which deadline names a held call when its batch's deadline passes too (#1541).

The rule: the gate that stopped the call names it.

- The batch budget is spent before the call reaches the approval gate: the
  budget gate refuses it, `TimeoutError`, and nobody is asked to approve.
- The call is already held when the batch deadline passes: the hold is not
  cut short. The hold's own outcome names the call -- `approval_timeout` if
  nobody answers, `approval_denied` if the approver says no. An approval that
  arrives after the deadline does not dispatch the call: it reads
  `CancellationError`.

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
from collections.abc import Iterator
from concurrent.futures import as_completed
from typing import Any
from unittest.mock import Mock, patch

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


def _deadline_while_held(world: World, answer: bool | None) -> BatchResult:
    """Run one batch whose deadline passes while its call is held, then answer `answer` (None: never)."""
    world.approver.answer = None  # the double never answers on its own here
    held_at_deadline: list[bool] = []

    def deadline_after_the_hold(fs: Any, timeout: float | None = None) -> Iterator[Any]:
        held_at_deadline.append(world.approver.wait_until_held(1, timeout=10))
        return as_completed(fs, timeout=0)

    def answer_after_the_deadline(**_: Any) -> None:
        if answer is None:
            return
        service = world.approver.service
        assert service is not None
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
