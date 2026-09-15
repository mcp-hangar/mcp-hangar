"""An approval-held call leaves no event loop, thread or file descriptor behind (#1452).

The approval wait is synchronous on a batch worker thread, so the gate's async
service runs on an event loop of its own. That loop used to be kept per thread
and closed only at interpreter exit. `execute()` builds a new ThreadPoolExecutor
for every batch, so nearly every held call ran on a fresh thread and left a loop
behind: its selector's file descriptors, and the idle `asyncio_0` thread the
hold registry's `asyncio.to_thread` wait had started in the loop's default
executor. Threads and descriptors grew with every held call until exit.

Driven through `BatchExecutor.execute` with the real `ApprovalGateService` and
hold registry, and a human answering from another loop on another thread -- the
shape of the REST handler resolving on FastMCP's loop.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
import os
import threading
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.read_models.tool_projection import reset_tool_projection_registry
from mcp_hangar.approvals import service as service_mod
from mcp_hangar.approvals.hold_registry import ApprovalHoldRegistry
from mcp_hangar.approvals.models import ApprovalRequest
from mcp_hangar.approvals.service import ApprovalGateService
from mcp_hangar.domain.services.tool_access_resolver import get_tool_access_resolver, reset_tool_access_resolver
from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy
from mcp_hangar.server.tools.batch import BatchExecutor, CallResult, CallSpec
from mcp_hangar.server.tools.batch.executor import _run_approval_coroutine

SERVER = "ledger"
TOOL = "transfer"

#: Batches per outcome. Twenty is enough to tell "returns to baseline" from
#: "grows by one loop per held call" at a glance.
N = 20


# --- counting ------------------------------------------------------------------


def _open_fds() -> int:
    """This process's open file descriptors. `/dev/fd` is the per-process view on macOS."""
    for fd_dir in ("/proc/self/fd", "/dev/fd"):
        if os.path.isdir(fd_dir):
            return len(os.listdir(fd_dir))
    pytest.skip("no way to count this process's file descriptors on this platform")


@dataclass(frozen=True)
class Counts:
    threads: int
    fds: int
    names: tuple[str, ...]

    @classmethod
    def now(cls) -> Counts:
        return cls(threading.active_count(), _open_fds(), tuple(sorted(t.name for t in threading.enumerate())))


# --- collaborators -------------------------------------------------------------


class _Store:
    """In-memory approval repository, as the other approval tests use."""

    def __init__(self) -> None:
        self._rows: dict[str, ApprovalRequest] = {}

    async def save(self, request: ApprovalRequest) -> None:
        self._rows[request.approval_id] = request

    async def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._rows.get(approval_id)

    async def update_state(self, approval_id, state, decided_by, decided_at, reason) -> None:
        row = self._rows[approval_id]
        row.state, row.decided_by, row.decided_at, row.reason = state, decided_by, decided_at, reason


class _Approver:
    """The delivery channel, with a human who answers from another thread's loop.

    ``answer=None`` never answers, so the hold runs to its timeout.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self.answer: bool | None = True
        self.service: ApprovalGateService | None = None

    async def send(self, request: ApprovalRequest) -> None:
        if self.answer is None or self.service is None:
            return
        reason = None if self.answer else "no"
        asyncio.run_coroutine_threadsafe(
            self.service.resolve(request.approval_id, self.answer, "approver-1", reason), self._loop
        )


@dataclass
class World:
    approver: _Approver


@contextmanager
def served_world() -> Iterator[World]:
    """A gateway whose one tool is held for a human, and the human's own loop."""
    reset_tool_access_resolver()
    reset_tool_projection_registry()

    human_loop = asyncio.new_event_loop()
    human = threading.Thread(target=human_loop.run_forever, name="approver-loop", daemon=True)
    human.start()

    approver = _Approver(human_loop)
    approver.service = ApprovalGateService(
        repository=_Store(), hold_registry=ApprovalHoldRegistry(), event_bus=Mock(), delivery=approver
    )

    server = Mock(state=Mock(value="ready"), has_tools=False, health=Mock(should_degrade=Mock(return_value=False)))
    server.id.value = SERVER
    context = Mock()
    context.command_bus.send.return_value = {"ok": True}
    context.governed_task_store = None
    context.approval_gate = approver.service
    context.repository.get.return_value = None  # no L7 policy: the tool access policy asks
    context.get_mcp_server.return_value = server
    context.mcp_server_exists.return_value = True
    try:
        with (
            patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=context),
            patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=context),
        ):
            yield World(approver)
    finally:
        human_loop.call_soon_threadsafe(human_loop.stop)
        human.join(timeout=5)
        human_loop.close()
        reset_tool_access_resolver()
        reset_tool_projection_registry()


def _hold_for(seconds: int) -> None:
    get_tool_access_resolver().set_mcp_server_policy(
        SERVER, ToolAccessPolicy(approval_list=(TOOL,), approval_timeout_seconds=seconds)
    )


def _one_batch(global_timeout: float = 30.0) -> CallResult:
    batch = BatchExecutor().execute(
        batch_id="b",
        calls=[CallSpec(index=0, call_id="c-1", mcp_server=SERVER, tool=TOOL, arguments={"amount": 10})],
        max_concurrency=1,
        global_timeout=global_timeout,
        fail_fast=False,
    )
    return batch.results[0]


def _concurrently(n: int, batch: Callable[[], CallResult]) -> list[CallResult]:
    """`n` separate batches from `n` callers at once, so `n` one-second holds take one second."""
    results: list[CallResult] = []
    lock = threading.Lock()

    def caller() -> None:
        result = batch()
        with lock:
            results.append(result)

    callers = [threading.Thread(target=caller, name=f"caller-{i}") for i in range(n)]
    for t in callers:
        t.start()
    for t in callers:
        t.join(timeout=30)
    assert len(results) == n, "a batch did not finish"
    return results


# --- the outcomes ----------------------------------------------------------------


def _approved(world: World, n: int) -> list[CallResult]:
    world.approver.answer = True
    _hold_for(30)  # answered at once; the timeout only bounds a broken test
    return [_one_batch() for _ in range(n)]


def _denied(world: World, n: int) -> list[CallResult]:
    world.approver.answer = False
    _hold_for(30)
    return [_one_batch() for _ in range(n)]


def _timed_out(world: World, n: int) -> list[CallResult]:
    world.approver.answer = None
    _hold_for(1)
    return _concurrently(n, _one_batch)


def _cancelled(world: World, n: int) -> list[CallResult]:
    # The batch's own deadline passes while the call is held; the batch is
    # cancelled, and the held call still runs to its approval timeout.
    world.approver.answer = None
    _hold_for(1)
    return _concurrently(n, lambda: _one_batch(global_timeout=0.2))


@dataclass(frozen=True)
class Outcome:
    run: Callable[[World, int], list[CallResult]]
    succeeded: bool
    error_type: str | None


OUTCOMES = {
    "approved": Outcome(_approved, True, None),
    "denied": Outcome(_denied, False, "approval_denied"),
    "timed_out": Outcome(_timed_out, False, "approval_timeout"),
    "cancelled_batch": Outcome(_cancelled, False, "approval_timeout"),
}


def _fill_the_publish_pool() -> None:
    """Start every thread of the approval service's bounded publish pool now.

    It grows on demand up to its cap and then stays, which is correct -- but a
    thread it starts during the measured run would read as a leak.
    """
    pool = service_mod._publish_executor
    workers = pool._max_workers
    barrier = threading.Barrier(workers)
    for future in [pool.submit(barrier.wait, 5) for _ in range(workers)]:
        future.result(timeout=10)


# --- the regression ----------------------------------------------------------------


@pytest.mark.parametrize("name", OUTCOMES)
def test_held_calls_leave_threads_and_descriptors_at_baseline(name: str) -> None:
    """N held calls in N batches: nothing they started outlives them."""
    outcome = OUTCOMES[name]
    with served_world() as world:
        _fill_the_publish_pool()
        outcome.run(world, 2)  # warm-up: whatever is lazily built once, and kept, is built now
        baseline = Counts.now()

        results = outcome.run(world, N)

        after = Counts.now()

    for result in results:
        assert result.success is outcome.succeeded, result
        assert result.error_type == outcome.error_type, result
    grew = dict(Counter(after.names) - Counter(baseline.names))  # e.g. {'asyncio_0': 20} before #1452
    assert after.threads <= baseline.threads, (
        f"{after.threads - baseline.threads} threads outlived {N} held calls: {grew}"
    )
    assert after.fds <= baseline.fds, f"{after.fds - baseline.fds} file descriptors outlived {N} held calls"


# --- the runner ----------------------------------------------------------------------


def test_each_run_gets_its_own_loop_and_closes_it() -> None:
    async def which_loop() -> asyncio.AbstractEventLoop:
        return asyncio.get_running_loop()

    first = _run_approval_coroutine(which_loop())
    second = _run_approval_coroutine(which_loop())

    assert first is not second
    assert first.is_closed() and second.is_closed()


def test_the_default_executor_thread_is_joined_before_it_returns() -> None:
    """The thread `ApprovalHoldRegistry.wait_slice` waits in is gone when the wait is."""

    async def wait_in_the_default_executor() -> threading.Thread:
        return await asyncio.to_thread(threading.current_thread)

    worker = _run_approval_coroutine(wait_in_the_default_executor())

    assert worker is not threading.current_thread()
    assert not worker.is_alive()


def test_a_failing_run_still_closes_its_loop() -> None:
    """The gate-error path: the store raises inside the loop, and the loop is closed anyway."""
    seen: list[asyncio.AbstractEventLoop] = []

    async def fail() -> Any:
        seen.append(asyncio.get_running_loop())
        await asyncio.to_thread(lambda: None)
        raise OSError("approval store unavailable")

    with pytest.raises(OSError, match="approval store unavailable"):
        _run_approval_coroutine(fail())

    assert seen and seen[0].is_closed()
