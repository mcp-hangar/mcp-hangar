"""Tell the application that an upstream refreshed its own tool catalogue (#1366).

An upstream announces a changed catalogue with ``notifications/tools/list_changed``
on the standing GET stream (#882), which arrives on the aggregate's reader
thread. The aggregate answers by re-listing (``McpServer._refresh_tools``), but
the front door does not serve the aggregate's catalogue: it serves the tool
projection registry, which only the ``McpServerStarted`` handler filled. So a
client told to re-list was handed the catalogue from the last start.

This module is the seam between the two, the way :mod:`subscription_relay` is
for the client-facing half: the application registers one listener at bootstrap
(the same projection handler that runs on ``McpServerStarted``), and the
aggregate announces a refreshed catalogue here. The domain never learns what a
projection is. With nothing registered an announcement is a no-op.
"""

from __future__ import annotations

import threading
from collections.abc import Callable

#: listener(mcp_server_id) -> None. Reads the refreshed catalogue itself.
CatalogueListener = Callable[[str], None]

_lock = threading.Lock()
_listener: CatalogueListener | None = None


def register_listener(listener: CatalogueListener) -> None:
    """Install the application's listener. The last registration wins."""
    global _listener
    with _lock:
        _listener = listener


def clear_listener() -> None:
    """Forget the listener; every later announcement is a no-op again."""
    global _listener
    with _lock:
        _listener = None


def announce(mcp_server_id: str) -> bool:
    """Say that *mcp_server_id*'s tool catalogue was just refreshed.

    Returns whether a listener took it. Called without the aggregate's lock
    held, because the listener reads the aggregate. A listener that raises is
    left to the caller's fault barrier, matching :func:`subscription_relay.forward`.
    """
    with _lock:
        listener = _listener
    if listener is None:
        return False
    listener(mcp_server_id)
    return True
