"""Push ``notifications/tools/list_changed`` on the handshake-era session (#1366, part A).

A client that lists once and caches kept whatever the front door held at that
moment, which during the boot warm-up is nothing, because the handshake era
advertised ``tools.listChanged: false`` and nothing was ever pushed. This module
is the push, and the advertisement that goes with it.

The flag
--------
``tools.listChanged`` on the handshake era (<= 2025-11-25) is derived from two
facts, both of which must hold:

* a handshake-era push channel is served in this process (:func:`serve_push_channel`,
  called by ``run_stdio``: a stdio connection is one duplex pipe, so the
  session the client initialized on is the channel), and
* the projection-change publisher is registered (:func:`register_publisher`,
  front_door only, because only front_door serves the projection).

So it is true on front_door stdio, false in egress, and false on front_door
HTTP, which since #877 is served stateless and has no back-channel until the
sessionless ``GET /mcp`` stream exists. Derived, not inverted (#888): the SDK
derives the handshake-era flag from the ``NotificationOptions`` passed to
``create_initialization_options``, which nothing passed, so the wrapper below
passes the one it derives. ``resources.subscribe``, ``resources.listChanged``
and ``prompts.listChanged`` stay false on this era, and all four 2026-07-28
flags still follow ``subscriptions/listen`` (see ``subscription_relay``),
because the SDK ignores ``NotificationOptions`` there.

Delivery
--------
A channel is recorded when it lists tools, with the projection it was served.
The registry calls :func:`_on_projection_changed` after any mutation, on
whichever thread made it; that only hands off to the serving loop. On the loop,
changes are coalesced over a trailing :data:`COALESCE_WINDOW_S` and then each
channel's tenant projection is generated once and compared with what that
channel last saw. Only a channel whose projection differs is notified, so a
change to tenant A's projection never reaches tenant B, and N upstreams landing
at once cost each channel at most one notification per window. The end of the
boot warm-up flushes at once (:func:`flush_now`) rather than waiting out the
window.

Per replica (#877): a channel is notified by the process that holds it, about
that process's catalogue. There is no fan-out across replicas.

#1231's bounded wait in ``flat_tool_projection`` stays as a backstop, for a
``tools/call`` naming a tool not projected yet and for a client that ignores
``list_changed``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from mcp_hangar._sdk_compat import is_modern_protocol_version, lowlevel_server

logger = logging.getLogger(__name__)

#: How long changes are gathered before channels are compared and notified. A
#: warm-up lands upstreams one after another; one notification for the lot is
#: what a client wants, and a third of a second is not a wait anyone sees.
COALESCE_WINDOW_S = 0.3

#: Channels held at most. stdio has one; the cap is for the transports to come.
MAX_CHANNELS = 1024

_push_transport: str | None = None
_publisher_registered = False


@dataclass
class _Channel:
    tenant_id: str | None
    send: Callable[[], Awaitable[None]]
    #: The projection this channel was last served or told about.
    seen: Any


# Touched only on the serving loop, except the reads in `_on_projection_changed`,
# which read a reference and never iterate.
_channels: dict[int, _Channel] = {}
_loop: asyncio.AbstractEventLoop | None = None
_pending: asyncio.TimerHandle | None = None
#: Running comparisons, held so the loop cannot drop them half-way.
_tasks: set[asyncio.Task[None]] = set()


def serve_push_channel(transport: str) -> None:
    """Record that this process serves a handshake-era channel it can push on."""
    global _push_transport
    _push_transport = transport


def advertises_tools_list_changed() -> bool:
    """The handshake-era ``tools.listChanged``: a push channel AND the publisher."""
    return _push_transport is not None and _publisher_registered


def register_publisher(mcp: Any) -> None:
    """Listen for projection changes and derive the handshake-era flag on *mcp*. front_door only."""
    global _publisher_registered
    from mcp.server.lowlevel.server import NotificationOptions

    from ..application.read_models.tool_projection import add_change_listener

    low = lowlevel_server(mcp)
    original = getattr(low, "create_initialization_options", None)
    if original is None:
        # Nothing to derive the flag on: leave it false and publish nothing.
        logger.warning("tool_list_changed_seam_unavailable")
        return

    def _derived(notification_options: Any = None, *args: Any, **kwargs: Any) -> Any:
        if notification_options is None:
            notification_options = NotificationOptions(tools_changed=advertises_tools_list_changed())
        return original(notification_options, *args, **kwargs)

    low.create_initialization_options = _derived
    add_change_listener(_on_projection_changed)
    _publisher_registered = True


def track_listing(mcp_ctx: Any, projection: Any) -> None:
    """Record the channel a handshake-era listing came in on, with what it was served.

    Runs on the serving loop, from the ``tools/list`` handler. A no-op unless the
    flag is advertised, so no channel is held where nothing was promised.
    """
    global _loop
    if not advertises_tools_list_changed():
        return
    session = getattr(mcp_ctx, "session", None)
    if session is None or is_modern_protocol_version(getattr(mcp_ctx, "protocol_version", None)):
        return
    # One session object per request; the connection is what persists (SDK seam).
    key = id(getattr(session, "_connection", session))
    channel = _channels.get(key)
    if channel is None:
        if len(_channels) >= MAX_CHANNELS:
            logger.warning("tool_list_changed_channel_cap_reached cap=%d", MAX_CHANNELS)
            return
        _channels[key] = _Channel(projection.tenant_id, session.send_tool_list_changed, projection)
    else:
        channel.seen = projection
    _loop = asyncio.get_running_loop()
    # A change that landed between generating this listing and recording it
    # either found no channel to schedule for, or was compared against the
    # previous listing and is now overwritten. One coalesced comparison closes both.
    _schedule()


def _on_projection_changed() -> None:
    """Registry listener: hand the change to the serving loop. Any thread."""
    loop = _loop
    if loop is None or not _channels:
        return
    try:
        loop.call_soon_threadsafe(_schedule)
    except RuntimeError:  # the loop is closed: the process is going away
        pass


def flush_now() -> None:
    """Compare and notify now rather than at the end of the window. Any thread."""
    loop = _loop
    if loop is None:
        return
    try:
        loop.call_soon_threadsafe(_fire)
    except RuntimeError:
        pass


def _schedule() -> None:
    """Open a window unless one is open. Loop thread."""
    global _pending
    if _pending is None and _loop is not None:
        _pending = _loop.call_later(COALESCE_WINDOW_S, _fire)


def _fire() -> None:
    """Close the window and start the comparison. Loop thread."""
    global _pending
    if _pending is not None:
        _pending.cancel()
        _pending = None
    if _channels and _loop is not None:
        task = _loop.create_task(_notify_changed())
        _tasks.add(task)
        task.add_done_callback(_tasks.discard)


async def _notify_changed() -> None:
    """Notify each channel whose tenant's projection differs from what it last saw."""
    from .flat_tool_projection import generate_projection

    current: dict[str | None, Any] = {}
    for key, channel in list(_channels.items()):
        tenant_id = channel.tenant_id
        if tenant_id not in current:
            try:
                current[tenant_id] = generate_projection(tenant_id)
            except Exception:  # noqa: BLE001 -- fault-barrier: a failed generation notifies nobody
                logger.warning("tool_list_changed_generation_failed", exc_info=True)
                current[tenant_id] = None
        now = current[tenant_id]
        if now is None or now == channel.seen:
            continue
        channel.seen = now
        try:
            await channel.send()
        except Exception:  # noqa: BLE001 -- fault-barrier: a dead channel is dropped, the rest still hear
            logger.debug("tool_list_changed_send_failed", exc_info=True)
            _channels.pop(key, None)


def reset() -> None:
    """Forget every channel and fact. For tests, and for a re-bootstrapped process."""
    global _push_transport, _publisher_registered, _loop, _pending
    from ..application.read_models.tool_projection import remove_change_listener

    remove_change_listener(_on_projection_changed)
    if _pending is not None:
        _pending.cancel()
    _push_transport = None
    _publisher_registered = False
    _loop = None
    _pending = None
    _channels.clear()
