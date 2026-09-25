"""Push ``notifications/tools/list_changed`` on the handshake-era session (#1366, part A).

A client that lists once and caches kept whatever the front door held at that
moment, which during the boot warm-up is nothing, because the handshake era
advertised ``tools.listChanged: false`` and nothing was ever pushed. This module
is the push, and the advertisement that goes with it.

The flag
--------
``tools.listChanged`` on the handshake era (<= 2025-11-25) is derived from two
facts, both of which must hold:

* a handshake-era push channel is served in this process (:func:`serve_push_channel`).
  ``run_stdio`` serves one because a stdio connection is one duplex pipe, so the
  session the client initialized on is the channel. ``mcp_app_for_serving``
  serves one because it mounts the sessionless ``GET /mcp`` stream
  (:mod:`tool_list_changed_stream`), which is the channel on HTTP; and
* the projection-change publisher is registered (:func:`register_publisher`,
  front_door only, because only front_door serves the projection).

So it is true on front_door stdio and front_door HTTP, and false in egress.
Derived, not inverted (#888): the SDK
derives the handshake-era flag from the ``NotificationOptions`` passed to
``create_initialization_options``, which nothing passed, so the wrapper below
passes the one it derives. ``resources.subscribe``, ``resources.listChanged``
and ``prompts.listChanged`` stay false on this era, and all four 2026-07-28
flags still follow ``subscriptions/listen`` (see ``subscription_relay``),
because the SDK ignores ``NotificationOptions`` there.

Delivery
--------
On stdio a channel is recorded when it lists tools, with the projection it was
served. On HTTP a channel is a ``GET /mcp`` stream, recorded when it opens
(:func:`open_stream`) with the projection generated then; the stateless POST a
listing arrives on is gone once it is answered, so it is never a channel.
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

from .. import metrics as prometheus_metrics

logger = logging.getLogger(__name__)

#: How long changes are gathered before channels are compared and notified. A
#: warm-up lands upstreams one after another; one notification for the lot is
#: what a client wants, and a third of a second is not a wait anyone sees.
COALESCE_WINDOW_S = 0.3

#: Channels held at most, across every caller. stdio has one; HTTP has one per open stream.
MAX_CHANNELS = 1024

#: Open ``GET /mcp`` streams one principal may hold. One API key is often shared
#: by a team's clients, each of which opens one stream; past this the stream is
#: refused and the client falls back to #1231's wait.
MAX_STREAMS_PER_PRINCIPAL = 32

#: Open ``GET /mcp`` streams one tenant may hold, so no tenant's principals can
#: take every one of :data:`MAX_CHANNELS` and leave the other tenants none.
MAX_STREAMS_PER_TENANT = 256

_push_transport: str | None = None
_publisher_registered = False


@dataclass
class _Channel:
    tenant_id: str | None
    send: Callable[[], Awaitable[None]]
    #: The projection this channel was last served or told about.
    seen: Any
    #: Who opened it, for the per-principal cap. None on stdio.
    owner: str | None = None
    #: Whether it is an HTTP stream, which the per-tenant cap counts.
    stream: bool = False
    #: The principal id a revocation names, and how to end the stream. HTTP only.
    principal: str | None = None
    end: Callable[[], None] | None = None


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
    if getattr(mcp_ctx, "request", None) is not None:
        # A stateless HTTP POST: answered and gone. Its channel is the GET stream.
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


def open_stream(
    key: int,
    tenant_id: str | None,
    owner: str,
    send: Callable[[], Awaitable[None]],
    *,
    principal: str | None = None,
    end: Callable[[], None] | None = None,
) -> str | None:
    """Record a ``GET /mcp`` stream as a channel, or say why not. Serving loop.

    The projection it is compared against is generated now, so a change that
    lands after this is told. The stream sends one ``list_changed`` as it
    opens, which covers anything that landed between the client's listing and
    this call.

    Returns:
        None when recorded; otherwise why not: ``"channel_cap"``,
        ``"tenant_cap"``, ``"principal_cap"`` or ``"generation_failed"``.
    """
    global _loop
    from .flat_tool_projection import generate_projection

    streams = [channel for channel in _channels.values() if channel.stream]
    if len(_channels) >= MAX_CHANNELS:
        return "channel_cap"
    if sum(1 for channel in streams if channel.tenant_id == tenant_id) >= MAX_STREAMS_PER_TENANT:
        return "tenant_cap"
    if sum(1 for channel in streams if channel.owner == owner) >= MAX_STREAMS_PER_PRINCIPAL:
        return "principal_cap"
    try:
        seen = generate_projection(tenant_id)
    except Exception:  # noqa: BLE001 -- fault-barrier: a stream that cannot be compared is not opened
        logger.warning("tool_list_changed_generation_failed", exc_info=True)
        return "generation_failed"
    _channels[key] = _Channel(tenant_id, send, seen, owner, stream=True, principal=principal, end=end)
    _loop = asyncio.get_running_loop()
    _count_streams()
    return None


def close_stream(key: int) -> None:
    """Forget a stream that ended. Serving loop."""
    _channels.pop(key, None)
    _count_streams()


def _count_streams() -> None:
    prometheus_metrics.TOOL_LIST_CHANGED_STREAMS.set(sum(1 for channel in _channels.values() if channel.stream))


def end_streams(principal_id: str) -> None:
    """End every stream *principal_id* holds, so its reconnect authenticates again. Any thread."""
    loop = _loop
    if loop is None or not _channels:
        return

    def _end() -> None:
        for channel in list(_channels.values()):
            if channel.principal == principal_id and channel.end is not None:
                channel.end()

    try:
        loop.call_soon_threadsafe(_end)
    except RuntimeError:  # the loop is closed: the process is going away
        pass


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
            prometheus_metrics.TOOL_LIST_CHANGED_NOTIFICATIONS_TOTAL.inc(
                transport="http" if channel.stream else "stdio"
            )
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
    _count_streams()
