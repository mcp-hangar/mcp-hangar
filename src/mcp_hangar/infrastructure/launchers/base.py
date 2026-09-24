"""Base mcp_server launcher interface."""

from __future__ import annotations

from typing import Any, ClassVar

from ...observability.conventions import McpServer
from ...observability.tracing import get_tracer


class McpServerLauncher:
    """Infrastructure base class for mcp_server launchers.

    ``launch`` is a template method: it opens ``mcp_server.launch`` and calls
    the subclass's ``_launch`` inside it, so spawning the process, running the
    container or opening the HTTP transport is its own span on a cold start
    (#1546), between the leader's ``mcp_server.cold_start`` and the handshake's
    ``initialize``. The four launchers take four different argument lists, which
    is why the span lives here rather than in each body.

    The caller passes ``mcp_server_id`` and ``mcp_server_mode`` so the span
    carries the server's own mode, the one the cold start metric uses: docker
    and podman servers both run on ``ContainerLauncher``, so the class cannot
    say which it is. A failed launch is recorded by the tracer itself.
    """

    #: Whether ``_launch`` itself takes ``mcp_server_id``. The caller passes the
    #: id to every launcher so the span can carry it; a launcher that has no use
    #: for it does not receive it.
    _launch_takes_server_id: ClassVar[bool] = False

    def launch(self, *args: Any, **kwargs: Any) -> Any:
        """Launch a mcp_server and return its transport client."""
        server_id = kwargs.get("mcp_server_id")
        if not self._launch_takes_server_id:
            kwargs.pop("mcp_server_id", None)
        mode = kwargs.pop("mcp_server_mode", None)
        with get_tracer(__name__).start_as_current_span("mcp_server.launch") as span:
            if server_id is not None:
                span.set_attribute(McpServer.ID, str(server_id))
            if mode is not None:
                span.set_attribute(McpServer.MODE, str(mode))
            return self._launch(*args, **kwargs)

    def _launch(self, *args: Any, **kwargs: Any) -> Any:
        """Do the launch. Each launcher implements this with its own arguments."""
        raise NotImplementedError

    def stop(self, mcp_server_id: str) -> None:
        """Stop a mcp_server previously launched by this launcher."""
        _ = mcp_server_id
