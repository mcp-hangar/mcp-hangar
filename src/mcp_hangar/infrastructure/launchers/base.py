"""Base mcp_server launcher interface."""

from __future__ import annotations

from typing import Any, ClassVar

from ...observability.conventions import McpServer
from ...observability.tracing import get_tracer, mark_span_error


class McpServerLauncher:
    """Infrastructure base class for mcp_server launchers.

    ``launch`` is a template method: it opens ``mcp_server.launch`` and calls
    the subclass's ``_launch`` inside it, so spawning the process, running the
    container or opening the HTTP transport is its own span on a cold start
    (#1546), between the leader's ``mcp_server.cold_start`` and the handshake's
    ``initialize``. The four launchers take four different argument lists, which
    is why the span lives here rather than in each body.
    """

    #: The mode this launcher runs, recorded as ``mcp.server.mode``.
    mode: ClassVar[str] = "unknown"

    #: Whether ``_launch`` itself takes ``mcp_server_id``. The caller passes the
    #: id to every launcher so the span can carry it; a launcher that has no use
    #: for it does not receive it.
    _launch_takes_server_id: ClassVar[bool] = False

    def launch(self, *args: Any, **kwargs: Any) -> Any:
        """Launch a mcp_server and return its transport client."""
        server_id = kwargs.get("mcp_server_id")
        if not self._launch_takes_server_id:
            kwargs.pop("mcp_server_id", None)
        with get_tracer(__name__).start_as_current_span(
            "mcp_server.launch", record_exception=False, set_status_on_exception=False
        ) as span:
            if server_id is not None:
                span.set_attribute(McpServer.ID, str(server_id))
            span.set_attribute(McpServer.MODE, self.mode)
            try:
                return self._launch(*args, **kwargs)
            except Exception as exc:
                mark_span_error(span, type(exc).__name__)
                raise

    def _launch(self, *args: Any, **kwargs: Any) -> Any:
        """Do the launch. Each launcher implements this with its own arguments."""
        raise NotImplementedError

    def stop(self, mcp_server_id: str) -> None:
        """Stop a mcp_server previously launched by this launcher."""
        _ = mcp_server_id
