"""A held call whose caller gave up is abandoned; one already granted still runs once (#2279).

The front door's flat call and `hangar_call` run the batch in a worker thread,
and a client that cancels the request ends only the await. Each call carries
the caller's own signal (`CallSpec.caller_gone`), which the approval hold reads
beside the batch's cancel event: once it is set, the hold ends and the record
turns `cancelled` (#2272), so nobody can approve a call the client was told
failed. The batch's other gates read the batch's event alone, so a call a human
granted before the client left runs, once.

Driven through `BatchExecutor.execute` with the real `ApprovalGateService` and
hold registry, as the hold's other regressions are.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from mcp_hangar.approvals.hold_registry import EitherSet
from mcp_hangar.approvals.models import ApprovalState
from mcp_hangar.server.tools.batch import BatchExecutor, CallResult, CallSpec, run_until_caller_gone
from mcp_hangar.server.tools.batch import executor as executor_mod
from tests.unit.test_an_approval_hold_leaves_nothing_behind import SERVER, TOOL, World, _hold_for, served_world


def _batch(caller_gone: threading.Event) -> CallResult:
    spec = CallSpec(
        index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={"amount": 10}, caller_gone=caller_gone
    )
    batch = BatchExecutor().execute(batch_id="b", calls=[spec], max_concurrency=1, global_timeout=30.0, fail_fast=False)
    return batch.results[0]


def _state(world: World) -> ApprovalState:
    service = world.approver.service
    assert service is not None
    [approval_id] = world.approver.approval_ids
    row = asyncio.run(service._repository.get(approval_id))
    assert row is not None
    return row.state


def test_a_held_call_is_abandoned_when_its_caller_gives_up() -> None:
    with served_world() as world:
        world.approver.answer = None  # nobody answers; only the caller's leaving ends the hold
        _hold_for(30)
        caller_gone = threading.Event()

        def leave_once_held() -> None:
            assert world.approver.wait_until_held(1, timeout=10)
            caller_gone.set()

        leaver = threading.Thread(target=leave_once_held)
        leaver.start()
        started = time.monotonic()
        result = _batch(caller_gone)
        leaver.join(timeout=10)

        assert not result.success
        assert result.error_type == "CancellationError", result
        assert time.monotonic() - started < 5, "the hold went on after the caller left"
        assert _state(world) is ApprovalState.CANCELLED
        executor_mod.get_context().command_bus.send.assert_not_called()


def test_a_granted_call_runs_once_though_its_caller_gave_up() -> None:
    with served_world() as world:
        world.approver.answer = True  # granted the moment it is held
        _hold_for(30)
        caller_gone = threading.Event()
        context = executor_mod.get_context()

        def upstream(*_args: object, **_kwargs: object) -> dict[str, bool]:
            caller_gone.set()  # the client leaves while the upstream runs the granted call
            return {"ok": True}

        context.command_bus.send.side_effect = upstream

        result = _batch(caller_gone)

        assert result.success, result
        assert context.command_bus.send.call_count == 1
        assert _state(world) is ApprovalState.APPROVED


def test_an_approval_recorded_before_the_caller_gave_up_still_runs_once() -> None:
    """Another process approves, and the caller leaves before the hold reads the store.

    The hold sees a decision from another process only at its next storage
    read, every two seconds, but it sees the caller leave within a step. Its
    compare-and-swap to `cancelled` then finds the record already `approved`:
    the approval came first, so the call runs, once. Only a cancelled batch
    turns an approval that came first into `cancelled` (#1702).
    """
    with served_world() as world:
        world.approver.answer = None  # the decision is written to the store, as the CLI does
        _hold_for(30)
        caller_gone = threading.Event()
        context = executor_mod.get_context()
        service = world.approver.service
        assert service is not None

        def approve_elsewhere_then_leave() -> None:
            assert world.approver.wait_until_held(1, timeout=10)
            [approval_id] = world.approver.approval_ids
            assert asyncio.run(
                service._repository.transition(
                    approval_id, (ApprovalState.PENDING,), ApprovalState.APPROVED, "cli-user", None, None
                )
            )
            caller_gone.set()

        other = threading.Thread(target=approve_elsewhere_then_leave)
        other.start()
        result = _batch(caller_gone)
        other.join(timeout=10)

        assert result.success, result
        assert context.command_bus.send.call_count == 1
        assert _state(world) is ApprovalState.APPROVED


def test_either_set_reads_set_while_either_event_is() -> None:
    first, second = threading.Event(), threading.Event()
    either = EitherSet(first, second)
    assert not either.is_set()
    assert not either.wait(0.01)
    second.set()
    assert either.is_set()
    assert either.wait(0)
    second.clear()
    first.set()
    assert either.is_set()


def test_run_until_caller_gone_tells_the_work_when_the_await_is_cancelled() -> None:
    seen: list[threading.Event] = []
    release = threading.Event()

    def work(caller_gone: threading.Event) -> str:
        seen.append(caller_gone)
        release.wait(5)
        return "done"

    async def cancel_it() -> None:
        task = asyncio.create_task(run_until_caller_gone(work))
        while not seen:
            await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(cancel_it())
    release.set()
    assert seen[0].is_set()

    seen.clear()
    release.set()
    assert asyncio.run(run_until_caller_gone(work)) == "done"
    assert not seen[0].is_set()
