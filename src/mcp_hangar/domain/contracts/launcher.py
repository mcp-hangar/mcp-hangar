"""Launcher contracts for mcp_server startup infrastructure."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from mcp_hangar.http_client import HttpClient
from mcp_hangar.stdio_client import StdioClient

LaunchResult = StdioClient | HttpClient


@runtime_checkable
class IMcpServerLauncher(Protocol):
    """Structural contract for infrastructure launchers."""

    def launch(self, *args: object, **kwargs: object) -> LaunchResult:
        """Launch a mcp_server transport client from mcp_server config."""
        ...

    def stop(self, mcp_server_id: str) -> None:
        """Stop a launched mcp_server, if the launcher tracks it."""
        ...
