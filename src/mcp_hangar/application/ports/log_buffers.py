"""Port for the per-server log buffer the logs API reads.

A command handler must not reach the buffer registry itself. `.importlinter`
puts `infrastructure` above `application`, and the registry -- with the
`ensure_log_buffer` / `release_log_buffer` helpers in
`server/bootstrap/logs.py` that wrap it -- sits above the handlers that load,
unload and delete a server. The handlers say *when* a server gains or loses its
buffer; the adapter behind this port says *how* (#1506).

An event handler was the alternative, and does not cover the case:
`McpServerDeregistered` is published by the delete handler alone, while
`hangar_unload` publishes `McpServerHotUnloaded`, and the load path publishes
no event that means "in the gateway" -- `McpServerRegistered` comes from the
create handler. One port called from the three places a hot-loaded server
arrives and departs is the whole of it.
"""

from __future__ import annotations

from typing import Any, Protocol


class ILogBuffers(Protocol):
    """Attaches and releases the per-server log buffer the logs API reads."""

    def attach(self, mcp_server_id: str, mcp_server: Any) -> bool:
        """Give *mcp_server* a buffer registered under *mcp_server_id*, unless it holds one.

        Call this BEFORE the server is started. The stderr reader that fills the
        buffer is spawned while the client is created, and only when a buffer is
        set by then (`McpServer._create_client`), so one attached afterwards is
        registered for the logs endpoint to read and never written to.

        A server that already holds a buffer keeps it, with the lines already in
        it: a running server's reader holds the buffer it was started with, and
        replacing the registered one would leave it filling a buffer nothing
        reads.

        Args:
            mcp_server_id: The id the buffer is registered under, and the one
                the logs endpoint looks it up by.
            mcp_server: The aggregate to inject the buffer into. Loosely typed
                because the load path builds it through an injected factory.

        Returns:
            Whether a buffer was attached. False means the server already held
            one.
        """
        ...

    def release(self, mcp_server_id: str) -> None:
        """Drop the buffer registered for a server that has left the gateway.

        The registry is a process-wide dict that outlives the server. Taking one
        out of the runtime store or the repository does not reach it, so an
        unloaded or deleted server's output stayed registered under an id that
        is free again (#1506).

        Args:
            mcp_server_id: The id whose registry entry is dropped. Unknown ids
                are ignored, so this is safe for a server that never had one.
        """
        ...
