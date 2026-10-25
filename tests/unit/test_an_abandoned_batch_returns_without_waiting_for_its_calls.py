"""A batch abandoned by an HTTP stop returns at once, with an MCP error (#2097).

The executor waited for every call, so a stop with a bound could not end a
batch whose upstream was slow. These drive the real `BatchExecutor.execute`
with a call that blocks until released.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar._sdk_compat import McpError
from mcp_hangar.server.tools.batch.executor import BatchExecutor
from mcp_hangar.server.tools.batch.models import CallResult, CallSpec
from mcp_hangar.server.tools.batch.shutdown import (
    BATCH_ABANDONED_CODE,
    abandon_in_flight_batches,
    in_flight_batch_count,
    reset_batch_abandonment,
)


@pytest.fixture(autouse=True)
def _accepting() -> Iterator[None]:
    reset_batch_abandonment()
    yield
    reset_batch_abandonment()


def _spec() -> CallSpec:
    return CallSpec(index=0, call_id="c1", mcp_server="slow", tool="sleep", arguments={})


def _execute(executor: BatchExecutor) -> Any:
    return executor.execute(batch_id="b1", calls=[_spec()], max_concurrency=1, global_timeout=30, fail_fast=False)


def test_an_abandoned_batch_raises_without_waiting_for_its_call() -> None:
    release = threading.Event()
    running = threading.Event()

    def blocking_call(self: Any, call: CallSpec, *args: Any) -> CallResult:
        running.set()
        release.wait(10)
        return CallResult(index=call.index, call_id=call.call_id, success=True, elapsed_ms=0.0)

    outcome: dict[str, Any] = {}

    def run() -> None:
        try:
            outcome["result"] = _execute(BatchExecutor())
        except McpError as exc:
            outcome["error"] = exc
        outcome["at"] = time.monotonic()

    with patch.object(BatchExecutor, "_execute_call", blocking_call):
        thread = threading.Thread(target=run)
        thread.start()
        assert running.wait(5)
        assert in_flight_batch_count() == 1

        abandoned_at = time.monotonic()
        assert abandon_in_flight_batches() == 1
        thread.join(5)
        release.set()

    assert not thread.is_alive()
    assert outcome["at"] - abandoned_at < 2
    error = outcome["error"]
    assert error.error.code == BATCH_ABANDONED_CODE
    assert error.error.data == {"reason": "shutdown", "batch_id": "b1"}
    assert in_flight_batch_count() == 0


def test_a_batch_started_after_the_abandonment_is_refused() -> None:
    abandon_in_flight_batches()

    with patch.object(BatchExecutor, "_execute_call") as call:
        with pytest.raises(McpError) as refused:
            _execute(BatchExecutor())

    call.assert_not_called()
    assert refused.value.error.code == BATCH_ABANDONED_CODE


def test_a_batch_that_is_not_abandoned_returns_when_its_call_does() -> None:
    def quick_call(self: Any, call: CallSpec, *args: Any) -> CallResult:
        return CallResult(index=call.index, call_id=call.call_id, success=True, elapsed_ms=0.0)

    started = time.monotonic()
    with patch.object(BatchExecutor, "_execute_call", quick_call):
        result = _execute(BatchExecutor())

    assert result.success is True
    # Not held until the batch timeout by the abandonment signal it also waits on.
    assert time.monotonic() - started < 5
