"""Whether the front door's boot-time warm-up is still running (#1231).

The front door warms every configured upstream at boot, on a thread of its own,
because gating the serving path on a backend handshake deadlocks the deployment
(#599, #878, #885). That is the right call and this module does not change it.

What it costs is the *first* answer. A client that connects while the warm-up is
in flight is served a catalogue with no upstream tools in it -- and over the
legacy handshake Hangar advertises ``tools.listChanged: false`` and sends no
notification afterwards, so a client that lists once at startup and caches (the
normal thing, and the correct thing given what it was told) keeps that empty
catalogue until it reconnects. The quickstart lands squarely in that window: it
says "restart your MCP client", which is exactly when a client lists.

So the fix is not to block the boot. It is to let the *listing* wait, briefly,
for the warm-up that is already running -- and only in the one case where the
answer would otherwise be knowably wrong:

* the caller has an identity (no identity is a fail-closed deny that must stay
  instant -- waiting there would slow down a refusal that is already correct),
* the projection came back empty because nothing has been discovered yet, not
  because policy filtered it,
* and the warm-up has not finished.

Every other empty listing is answered at once, as before. When the deadline
passes the listing is served with whatever exists: a bounded wait for a backend
that may never arrive, never an unbounded one.

Not a config knob on purpose. The wait is invisible when the warm-up is quick
and irrelevant when it is not, and a deployment that needs to tune it is really
asking for the notification path instead -- advertising ``listChanged`` and
pushing ``notifications/tools/list_changed`` when a catalogue lands, which is
the complete fix and a larger change than this one.
"""

from __future__ import annotations

import threading

#: How long a first, empty listing waits for the boot warm-up. Long enough for a
#: local subprocess upstream to finish its handshake (the quickstart's case
#: takes about a second), short enough that a client with a modest request
#: timeout is not the thing that breaks.
WARMUP_LIST_WAIT_S = 5.0

#: Polled rather than blocked on, so the wait never occupies a worker thread.
_POLL_INTERVAL_S = 0.05

#: Set while a warm-up is running. ``None`` means no warm-up was ever started --
#: an ``egress`` gateway, or a process that never booted one -- and in that case
#: nothing waits.
_in_flight: threading.Event | None = None


def warmup_started() -> None:
    """Record that the boot-time warm-up has begun."""
    global _in_flight
    _in_flight = threading.Event()


def warmup_finished() -> None:
    """Record that the boot-time warm-up has finished, successfully or not.

    Called from the warm-up thread's `finally`, so a warm-up that raises still
    releases every listing waiting on it.
    """
    if _in_flight is not None:
        _in_flight.set()


def is_warming() -> bool:
    """Is a boot-time warm-up running right now?"""
    return _in_flight is not None and not _in_flight.is_set()


def reset() -> None:
    """Forget any warm-up state. For tests, and for a re-bootstrapped process."""
    global _in_flight
    _in_flight = None


async def wait_for_catalogue(timeout: float = WARMUP_LIST_WAIT_S) -> bool:
    """Wait until the warm-up finishes, or *timeout* passes.

    Returns True if the warm-up finished within the deadline. Polls instead of
    blocking on the event so the caller's event loop keeps serving; the wait is
    rare (a first listing during boot) and short.
    """
    import anyio

    if _in_flight is None:
        return False

    deadline = timeout
    while deadline > 0:
        if _in_flight.is_set():
            return True
        step = min(_POLL_INTERVAL_S, deadline)
        await anyio.sleep(step)
        deadline -= step
    return _in_flight.is_set()
