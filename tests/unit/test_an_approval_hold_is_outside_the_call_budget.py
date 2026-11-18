"""The time a call is held for approval does not count against its budget (#2271).

A front door gave every flat call a 30 s budget, and the approval hold ran
inside it: a human who approved after 30 s saw the approval succeed, and the
call was refused `CancellationError` and never ran. `hangar_call`'s `timeout`
and the facade's `timeout_s` cut the hold short the same way.

The call's clock now stops while it is held. A call approved inside its
approval window runs exactly once, with the budget it had when the hold began;
one nobody approves is refused when its window ends and the approval is
recorded `expired`. A call nobody holds keeps the budget it always had.

Real clocks, no patched waits: each budget here is shorter than the hold, and
the approver answers after the budget has passed. The executor, the approval
service and its hold registry are the production ones
(`test_an_approval_hold_leaves_nothing_behind.served_world`).
"""

from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.facade import Hangar
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, hangar_call
from mcp_hangar.server.tools.batch import executor as executor_mod
from mcp_hangar.server.tools.batch.executor import BatchResult, _CallPipeline
from tests.unit.test_an_approval_hold_leaves_nothing_behind import SERVER, TOOL, World, _hold_for, served_world

#: How long after the call is held the approver answers: past every budget below.
_LATE_S = 2.5


def _approve_after(world: World, seconds: float) -> threading.Thread:
    """Approve the first held call *seconds* after it is held, from the approver's own loop."""

    def approve() -> None:
        assert world.approver.wait_until_held(1, timeout=10), "the call was never held"
        time.sleep(seconds)
        service = world.approver.service
        assert service is not None
        (approval_id,) = world.approver.approval_ids
        asyncio.run_coroutine_threadsafe(service.resolve(approval_id, True, "approver-1"), world.approver._loop).result(
            timeout=10
        )

    approver = threading.Thread(target=approve, name="late-approver", daemon=True)
    approver.start()
    return approver


def _batch(global_timeout: float) -> BatchResult:
    return BatchExecutor().execute(
        batch_id="b",
        calls=[CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={"amount": 10})],
        max_concurrency=1,
        global_timeout=global_timeout,
        fail_fast=False,
    )


def _record_state(world: World) -> ApprovalState:
    service = world.approver.service
    assert service is not None
    (approval_id,) = world.approver.approval_ids
    record = asyncio.run(service._repository.get(approval_id))
    return record.state


def test_a_call_approved_after_its_budget_runs_once_with_the_budget_it_had() -> None:
    with served_world() as world:
        context = executor_mod.get_context()
        world.approver.answer = None
        _hold_for(30)
        approver = _approve_after(world, _LATE_S)
        batch = _batch(global_timeout=2.0)
        approver.join(timeout=10)
        state = _record_state(world)

    result = batch.results[0]
    assert (result.success, result.error_type) == (True, None), result
    assert batch.elapsed_ms >= _LATE_S * 1000, batch.elapsed_ms
    (sent,) = context.command_bus.send.call_args_list  # exactly once
    # The wall clock left nothing of the 2 s budget; the hold is not counted.
    assert 0 < sent.args[0].timeout <= 2.0, sent.args[0].timeout
    assert state is ApprovalState.APPROVED


def test_a_call_nobody_approves_is_refused_when_its_window_ends_and_recorded_expired() -> None:
    with served_world() as world:
        context = executor_mod.get_context()
        world.approver.answer = None
        _hold_for(3)
        batch = _batch(global_timeout=2.0)
        state = _record_state(world)

    result = batch.results[0]
    assert (result.success, result.error_type) == (False, "approval_timeout"), result
    assert batch.elapsed_ms >= 3000, batch.elapsed_ms
    assert state is ApprovalState.EXPIRED
    context.command_bus.send.assert_not_called()


def test_hangar_call_with_a_short_timeout_runs_a_call_approved_after_it() -> None:
    with served_world() as world:
        context = executor_mod.get_context()
        world.approver.answer = None
        _hold_for(30)
        approver = _approve_after(world, _LATE_S)
        answer = hangar_call([{"mcp_server": SERVER, "tool": TOOL, "arguments": {"amount": 10}}], timeout=1)
        approver.join(timeout=10)

    (result,) = answer["results"]
    assert result["success"], answer
    assert context.command_bus.send.call_count == 1


def test_hangar_call_refuses_a_call_nobody_approves_when_its_window_ends() -> None:
    with served_world() as world:
        context = executor_mod.get_context()
        world.approver.answer = None
        _hold_for(3)
        answer = hangar_call([{"mcp_server": SERVER, "tool": TOOL, "arguments": {"amount": 10}}], timeout=1)
        state = _record_state(world)

    (result,) = answer["results"]
    assert (result["success"], result["error_type"]) == (False, "approval_timeout"), answer
    assert state is ApprovalState.EXPIRED
    context.command_bus.send.assert_not_called()


def test_the_facade_waits_for_a_call_approved_after_its_timeout() -> None:
    """`invoke` bounded its own wait at `timeout_s`, so it raised while the call still ran."""
    hangar = Hangar(config_path="config.yaml", _context=Mock())
    hangar._started = True
    try:
        with served_world() as world:
            context = executor_mod.get_context()
            world.approver.answer = None
            _hold_for(30)
            approver = _approve_after(world, _LATE_S)
            result = asyncio.run(hangar.invoke(SERVER, TOOL, {"amount": 10}, timeout_s=1.0))
            approver.join(timeout=10)
    finally:
        hangar._executor.shutdown(wait=True)

    assert result == {"ok": True}
    assert context.command_bus.send.call_count == 1


def test_the_facade_raises_a_timeout_the_call_itself_raised_at_once() -> None:
    """A `TimeoutError` the call raised is the call's outcome, not a reason to wait again."""
    hangar = Hangar(config_path="config.yaml", _context=Mock())
    hangar._started = True

    def timed_out(*_: Any, **__: Any) -> Any:
        raise TimeoutError("the call's own")

    try:
        with patch("mcp_hangar.server.tools.batch.call_as", timed_out):
            started = time.perf_counter()
            with pytest.raises(TimeoutError, match="the call's own"):
                asyncio.run(hangar.invoke(SERVER, TOOL, {}, timeout_s=5.0))
            waited = time.perf_counter() - started
    finally:
        hangar._executor.shutdown(wait=True)

    assert waited < 1.0, waited


def test_the_facade_still_times_out_a_call_nobody_holds() -> None:
    hangar = Hangar(config_path="config.yaml", _context=Mock())
    hangar._started = True
    released = threading.Event()
    try:
        with served_world():
            context = executor_mod.get_context()
            context.command_bus.send.side_effect = lambda *_: released.wait(10) or {"ok": True}
            started = time.perf_counter()
            with pytest.raises(TimeoutError):
                asyncio.run(hangar.invoke(SERVER, "unheld", {}, timeout_s=1.0))
            waited = time.perf_counter() - started
            released.set()
    finally:
        hangar._executor.shutdown(wait=True)

    assert 1.0 <= waited < 3.0, waited


# --- a call nobody holds keeps its budget ----------------------------------------


def _pipeline(global_timeout: float, held_s: float = 0.0, cancelled: bool = False) -> _CallPipeline:
    cancel = threading.Event()
    if cancelled:
        cancel.set()
    return _CallPipeline(
        call=CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={}),
        ctx=Mock(approval_gate=None),
        call_start=time.perf_counter(),
        cancel_event=cancel,
        global_timeout=global_timeout,
        batch_start_time=time.perf_counter() - 2.0,
        caller_tenant_id=None,
        resolver=Mock(),
        proj_registry=Mock(),
        tracer=Mock(),
        held_s=held_s,
    )


@pytest.mark.parametrize(
    ("global_timeout", "held_s", "cancelled", "refused"),
    [
        (1.0, 0.0, False, True),  # the budget is spent: refused, as the batch deadline used to refuse it
        (1.0, 5.0, False, False),  # spent on the wall clock, not counting the hold
        (60.0, 0.0, False, False),  # budget left and nobody cancelled the batch
        (60.0, 0.0, True, True),  # fail_fast or a stop cancelled the batch
    ],
)
def test_the_last_gate_before_dispatch_reads_the_calls_own_clock(
    global_timeout: float, held_s: float, cancelled: bool, refused: bool
) -> None:
    p = _pipeline(global_timeout, held_s, cancelled)

    refusal: Any = BatchExecutor()._gate_cancelled_after_cold_start(p)

    assert (refusal is not None) is refused, refusal
    if refusal is not None:
        assert refusal.error_type == "CancellationError"


def test_an_ungated_call_runs_on_the_budget_the_wall_clock_leaves() -> None:
    with served_world():
        context = executor_mod.get_context()
        batch = BatchExecutor().execute(
            batch_id="b",
            calls=[CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool="unheld", arguments={})],
            max_concurrency=1,
            global_timeout=1.0,
            fail_fast=False,
        )

    assert batch.results[0].success, batch.results[0]
    (sent,) = context.command_bus.send.call_args_list
    assert 0 < sent.args[0].timeout <= 1.0, sent.args[0].timeout
