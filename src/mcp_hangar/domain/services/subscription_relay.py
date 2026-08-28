"""Relay an upstream's change notifications to the clients that subscribed (#1027).

The upstream half of a subscription arrives on the standing GET stream (#882),
on the aggregate's reader thread. The client half lives in the front door,
which owns tenancy: which projected URI belongs to whom, and which listen
streams may see a given upstream at all. So this module is only the seam
between the two -- a registered sink, the way :mod:`progress_relay` is a
registered forwarder -- and the domain never learns what a listen stream is.

Registered by ``fastmcp_server.subscription_relay`` when the front door
installs its ``subscriptions/listen`` surface; with nothing registered every
forward is a no-op and the upstream notification is logged as unclaimed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

#: The upstream notifications a subscribed client can be told about. The
#: 2026-07-28 change-notification vocabulary (SEP-2575): a level trigger each,
#: carrying no state beyond "this changed, refetch if you care".
RELAYED_METHODS = (
    "notifications/resources/updated",
    "notifications/resources/list_changed",
    "notifications/prompts/list_changed",
    "notifications/tools/list_changed",
)

#: sink(mcp_server_id, method, params) -> whether any client stream took it.
UpstreamEventSink = Callable[[str, str, dict[str, Any]], bool]

_lock = threading.Lock()
_sink: UpstreamEventSink | None = None


def register_sink(sink: UpstreamEventSink) -> None:
    """Install the front door's publisher. The last registration wins."""
    global _sink
    with _lock:
        _sink = sink


def clear_sink() -> None:
    """Forget the publisher; every later forward is a no-op again."""
    global _sink
    with _lock:
        _sink = None


def forward(mcp_server_id: str, method: str, params: dict[str, Any]) -> bool:
    """Hand an upstream change notification to the front door.

    Returns whether a client stream took it -- ``False`` also when no sink is
    registered, which is the ordinary answer outside ``front_door`` mode.

    Runs on the GET stream's reader thread. A sink that raises is this module's
    caller's bug and is left to the stream's handler fault barrier, matching
    :func:`progress_relay.forward`.
    """
    with _lock:
        sink = _sink
    if sink is None:
        return False
    return sink(mcp_server_id, method, params)
