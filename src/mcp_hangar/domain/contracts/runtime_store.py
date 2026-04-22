"""Runtime mcp_server store contract.

Defines IRuntimeMcpServerStore so application layer can look up hot-loaded
(runtime) mcp_servers without depending on infrastructure.RuntimeMcpServerStore.
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mcp_server_runtime import McpServerRuntime


class IRuntimeMcpServerStore(ABC):
    """Read-only interface for looking up runtime (hot-loaded) mcp_servers.

    Application command and query handlers use this to check runtime mcp_servers
    in addition to the static repository.
    """

    @abstractmethod
    def get_mcp_server(self, mcp_server_id: str) -> "McpServerRuntime | None":
        """Return the runtime mcp_server with the given ID, or None if not found.

        Args:
            mcp_server_id: The mcp_server ID to look up.

        Returns:
            McpServerRuntime instance, or None if not found.
        """
