"""Base mcp_server launcher interface."""

from __future__ import annotations


class McpServerLauncher:
    """Infrastructure base class for mcp_server launchers."""

    def stop(self, mcp_server_id: str) -> None:
        """Stop a mcp_server previously launched by this launcher."""
        _ = mcp_server_id
