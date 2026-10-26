"""Whether the front door's boot-time warm-up is still running (#1231).

The front door warms every configured upstream at boot, on a thread of its own,
because gating the serving path on a backend handshake deadlocks the deployment
(#599, #878, #885). That is the right call and this module does not change it.

What it costs is the *first* answer. A client that connects while the warm-up is
in flight is served a catalogue with no upstream tools in it. Where the front
door can push (stdio, #1366), it now advertises ``tools.listChanged`` and tells
the client when the catalogue lands (``tool_list_changed``). This wait was the
mitigation before that existed, and it stays, deliberately, as a backstop for
the two cases a notification does not cover: a ``tools/call`` naming a tool not
projected yet, which no re-list precedes, and a client that ignores
``list_changed`` or has no channel to hear it on (front_door HTTP until the
sessionless GET stream lands). ``server/discover`` waits on the same condition:
it promises the caller's ``tools/list`` surface, and a stateless client has no
notification channel at all (#2101).

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

The warming state is bounded as well (#2122). A warm-up that never finishes,
say on an upstream handshake that never returns, otherwise kept every empty
listing waiting its full deadline for the life of the process. The front door
gives the warm-up a bound derived from the servers it starts
(`lifecycle.warm_up_bound_s`); when it passes, listings stop waiting and a
WARNING names the servers still warming. The warm-up itself goes on, and what
it starts late is projected and announced as before.

Not a config knob on purpose. The wait is invisible when the warm-up is quick
and irrelevant when it is not, and a deployment that needs to tune it is really
asking for the notification path, which is the complete fix.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from ..logging_config import get_logger

logger = get_logger(__name__)

#: How long a first, empty listing waits for the boot warm-up. Long enough for a
#: local subprocess upstream to finish its handshake (the quickstart's case
#: takes about a second), short enough that a client with a modest request
#: timeout is not the thing that breaks.
WARMUP_LIST_WAIT_S = 5.0

#: Polled rather than blocked on, so the wait never occupies a worker thread.
_POLL_INTERVAL_S = 0.05


@dataclass
class _WarmUp:
    """One boot warm-up: what a listing waits on, and what is left to warm."""

    #: Set when listings stop waiting: the warm-up finished, or its bound passed.
    released: threading.Event = field(default_factory=threading.Event)
    #: The servers the warm-up has not finished with yet, named in the WARNING.
    pending: set[str] = field(default_factory=set)
    #: True once the warm-up itself has ended, however late.
    finished: bool = False
    timer: threading.Timer | None = None
    #: Called once if the bound passes before the warm-up finishes (#2128).
    on_bound_passed: Callable[[], None] | None = None


#: The current warm-up. ``None`` means none was ever started -- an ``egress``
#: gateway, or a process that never booted one -- and in that case nothing waits.
_current: _WarmUp | None = None
_lock = threading.Lock()


def warmup_started(
    bound_s: float | None = None,
    mcp_server_ids: Iterable[str] = (),
    on_bound_passed: Callable[[], None] | None = None,
) -> None:
    """Record that the boot-time warm-up has begun.

    Called before the warm-up thread is started, and again by the warm-up
    itself; a warm-up already marked keeps its mark, so nothing waiting on it
    is handed a fresh one. That holds after its bound has passed too: the
    warm-up is still running, and marking it again would start the wait over
    with it (#2122).

    Args:
        bound_s: How long listings may wait on this warm-up at most. When it
            passes, listings stop waiting, and a WARNING names the servers that
            were still warming. ``None`` sets no bound.
        mcp_server_ids: The servers the warm-up will start, for that WARNING.
        on_bound_passed: Called once, on the timer's thread, if the bound passes
            before the warm-up finishes; never when it finishes in time. The
            front door starts the required-catalogue retry from it (#2128).
    """
    global _current
    with _lock:
        if _current is not None and not _current.finished:
            return
        warm_up = _WarmUp(pending=set(mcp_server_ids), on_bound_passed=on_bound_passed)
        if bound_s is not None:
            # A daemon: a hung warm-up must not also hold the process open on exit.
            warm_up.timer = threading.Timer(bound_s, _bound_passed, args=(warm_up, bound_s))
            warm_up.timer.daemon = True
            warm_up.timer.start()
        _current = warm_up


def server_warmed(mcp_server_id: str) -> None:
    """Record that the warm-up is done with *mcp_server_id*, started, failed or skipped."""
    with _lock:
        if _current is not None:
            _current.pending.discard(mcp_server_id)


def warmup_finished() -> None:
    """Record that the boot-time warm-up has finished, successfully or not.

    Called from the warm-up thread's `finally`, so a warm-up that raises still
    releases every listing waiting on it. A warm-up that finishes after its
    bound changes nothing here: listings already stopped waiting, and what it
    started is projected and announced as always.
    """
    with _lock:
        if _current is None:
            return
        _current.finished = True
        if _current.timer is not None:
            _current.timer.cancel()
        _current.released.set()


def _bound_passed(warm_up: _WarmUp, bound_s: float) -> None:
    """End the waiting on a warm-up that outlived its bound (#2122)."""
    with _lock:
        if warm_up is not _current or warm_up.released.is_set():
            return
        warm_up.released.set()
        still_warming = sorted(warm_up.pending)
    logger.warning(
        "front_door_warmup_bound_passed",
        bound_s=bound_s,
        still_warming=still_warming,
    )
    # Outside the lock, and only on the path that released the warm-up, so it
    # runs at most once and never for a warm-up that finished in time.
    if warm_up.on_bound_passed is not None:
        try:
            warm_up.on_bound_passed()
        except Exception as e:  # noqa: BLE001 -- fault-barrier: the timer thread must log, not die silently
            logger.error("front_door_warmup_bound_callback_failed", error_type=type(e).__name__)


def is_warming() -> bool:
    """Is a boot-time warm-up running, and are listings still waiting on it?"""
    warm_up = _current
    return warm_up is not None and not warm_up.released.is_set()


def reset() -> None:
    """Forget any warm-up state. For tests, and for a re-bootstrapped process."""
    global _current
    with _lock:
        if _current is not None and _current.timer is not None:
            _current.timer.cancel()
        _current = None


async def wait_for_catalogue(timeout: float = WARMUP_LIST_WAIT_S) -> bool:
    """Wait until the warm-up finishes or its bound passes, or *timeout* passes.

    Returns True if the waiting ended within the deadline. Polls instead of
    blocking on the event so the caller's event loop keeps serving; the wait is
    rare (a first listing during boot) and short.
    """
    import anyio

    warm_up = _current
    if warm_up is None:
        return False

    deadline = timeout
    while deadline > 0:
        if warm_up.released.is_set():
            return True
        step = min(_POLL_INTERVAL_S, deadline)
        await anyio.sleep(step)
        deadline -= step
    return warm_up.released.is_set()
