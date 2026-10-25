"""Abandoning the batches in flight when ``serve --http`` stops (#2097).

A batch blocks its caller until every call in it answers. A stop with a bound
(``http.graceful_shutdown_timeout_s``) cannot wait that long, and cancelling the
request task does not stop the thread a batch runs on: the gateway outlived the
bound by as long as its slowest upstream call. :func:`abandon_in_flight_batches`
is how the HTTP stop gives up on them once the bound has passed. Every batch in
flight returns at once without waiting for its calls, and raises the error
:func:`batch_abandoned_error` builds, which the client receives as a JSON-RPC
error rather than a cut connection. A call that had not reached its upstream
yet never does; one that had is left to the upstream, whose server the shutdown
stops next. From then on a new batch is refused with the same error.

Nothing here runs unless an HTTP stop with a bound runs out of time.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from concurrent.futures import FIRST_COMPLETED, Future, wait
from typing import Any

from ...._sdk_compat import make_mcp_error

#: The JSON-RPC error code an abandoned call is answered with: the start of the
#: range JSON-RPC reserves for server-defined errors.
BATCH_ABANDONED_CODE = -32000

_lock = threading.Lock()
_abandoned = False
_in_flight: dict[Future[None], threading.Event] = {}


def batch_abandoned_error(batch_id: str) -> Exception:
    """The error a batch abandoned by the shutdown raises: an MCP protocol error."""
    error: Exception = make_mcp_error(
        BATCH_ABANDONED_CODE,
        "The gateway is shutting down and abandoned this call before it finished; "
        "it may or may not have taken effect upstream",
        {"reason": "shutdown", "batch_id": batch_id},
    )
    return error


def track_batch(cancel_event: threading.Event) -> Future[None] | None:
    """Register a starting batch; return the future that completes if it is abandoned.

    Args:
        cancel_event: The batch's cancel event, set when it is abandoned so no
            call of it that has not started yet starts.

    Returns:
        The future to wait on alongside the batch's calls, or None when the
        shutdown has already abandoned the batches in flight and this one must
        not start.
    """
    with _lock:
        if _abandoned:
            return None
        signal: Future[None] = Future()
        _in_flight[signal] = cancel_event
        return signal


def untrack_batch(signal: Future[None]) -> None:
    """Forget a batch :func:`track_batch` registered, however it ended."""
    with _lock:
        _in_flight.pop(signal, None)


def wait_unless_abandoned(futures: Iterable[Future[Any]], signal: Future[None]) -> bool:
    """Wait for every one of *futures*, unless *signal* completes first.

    Returns:
        True when the batch was abandoned with some of *futures* still running.
    """
    pending: set[Future[Any]] = set(futures)
    while pending:
        if signal.done():
            return True
        _, pending = wait(pending | {signal}, return_when=FIRST_COMPLETED)
        pending.discard(signal)
    return False


def in_flight_batch_count() -> int:
    """How many batches are running now."""
    with _lock:
        return len(_in_flight)


def abandon_in_flight_batches() -> int:
    """Abandon every batch in flight and refuse every later one; return how many were abandoned."""
    global _abandoned
    with _lock:
        _abandoned = True
        batches = list(_in_flight.items())
    for signal, cancel_event in batches:
        cancel_event.set()
        if not signal.done():
            signal.set_result(None)
    return len(batches)


def reset_batch_abandonment() -> None:
    """Accept batches again, as a process does before its first stop. For tests."""
    global _abandoned
    with _lock:
        _abandoned = False


__all__ = [
    "BATCH_ABANDONED_CODE",
    "abandon_in_flight_batches",
    "batch_abandoned_error",
    "in_flight_batch_count",
    "reset_batch_abandonment",
    "track_batch",
    "untrack_batch",
    "wait_unless_abandoned",
]
