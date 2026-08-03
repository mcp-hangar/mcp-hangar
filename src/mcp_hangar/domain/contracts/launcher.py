"""Launcher contracts for mcp_server startup infrastructure."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class TransportClient(Protocol):
    """A live connection to a backend MCP server, as the domain sees it.

    Previously this was ``StdioClient | HttpClient`` -- a domain contract naming
    two concrete adapters, so a third transport could not be added without
    editing the domain, and the domain could not be read without reading two
    infrastructure modules.

    The union also did no work: the aggregate holds the launched client as
    ``Any | None``, with "StdioClient or HttpClient" in a comment beside it, so
    nothing was checked at the one place it mattered.

    These three methods are the entire surface the domain uses. ``timeout``
    carries a default because both transports have one and the domain always
    passes an explicit value; requiring it here would exclude a transport that
    sensibly defaults.
    """

    def is_alive(self) -> bool:
        """Whether the connection is still usable."""
        ...

    def close(self) -> None:
        """Tear the connection down. Must be safe to call more than once."""
        ...

    def call(self, method: str, params: dict[str, Any], timeout: float = ...) -> dict[str, Any]:
        """Issue a JSON-RPC call and return the decoded response envelope."""
        ...


#: What a launcher hands back. An alias rather than a second name, so existing
#: annotations keep reading naturally at the launcher boundary.
LaunchResult = TransportClient


@runtime_checkable
class IMcpServerLauncher(Protocol):
    """Structural contract for infrastructure launchers."""

    def launch(self, *args: object, **kwargs: object) -> LaunchResult:
        """Launch a mcp_server transport client from mcp_server config."""
        ...

    def stop(self, mcp_server_id: str) -> None:
        """Stop a launched mcp_server, if the launcher tracks it."""
        ...
