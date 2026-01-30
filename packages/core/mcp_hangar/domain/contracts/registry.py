"""Registry client contract for the official MCP server registry.

This module defines the interface for accessing the official MCP server registry
at registry.modelcontextprotocol.io. Implementations are provided by the
infrastructure layer.
"""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TransportInfo:
    """Transport configuration for connecting to an MCP server.

    Attributes:
        type: Transport type ("stdio" for subprocess, "sse" for HTTP/SSE).
        args: Optional command-line arguments for stdio transport.
    """

    type: str
    args: list[str] | None = None


@dataclass(frozen=True)
class PackageInfo:
    """Package information for installing an MCP server.

    Attributes:
        registry_type: Package registry type ("npm", "pypi", "oci", "mcpb").
        identifier: Package identifier (e.g., "@stripe/mcp-server", "mcp-server-time").
        version: Optional version constraint (e.g., "1.2.3", "^1.0.0").
        transport: Transport configuration for connecting to the server.
        file_sha256: Optional SHA256 hash for binary verification.
    """

    registry_type: str
    identifier: str
    version: str | None
    transport: TransportInfo
    file_sha256: str | None = None


@dataclass(frozen=True)
class ServerSummary:
    """Summary information about an MCP server from search results.

    Attributes:
        id: Unique server identifier in the registry.
        name: Human-readable server name.
        description: Brief description of the server's functionality.
        is_official: Whether this is an official/verified server.
    """

    id: str
    name: str
    description: str
    is_official: bool


@dataclass(frozen=True)
class ServerDetails:
    """Detailed information about an MCP server.

    Attributes:
        id: Unique server identifier in the registry.
        name: Human-readable server name.
        description: Full description of the server's functionality.
        vendor: Optional vendor/organization name.
        source_url: Optional URL to source repository.
        is_official: Whether this is an official/verified server.
        packages: Available package distributions for installation.
        required_env_vars: Environment variables required by the server.
    """

    id: str
    name: str
    description: str
    vendor: str | None
    source_url: str | None
    is_official: bool
    packages: list[PackageInfo]
    required_env_vars: list[str]


class IRegistryClient(Protocol):
    """Contract for accessing the official MCP server registry.

    Implementations should handle HTTP requests to the registry API,
    caching, and error handling.
    """

    async def search(self, query: str, limit: int = 10) -> list[ServerSummary]:
        """Search for MCP servers matching the query.

        Args:
            query: Search query string.
            limit: Maximum number of results to return (default: 10).

        Returns:
            List of matching server summaries.

        Raises:
            RegistryError: If the registry request fails.
        """
        ...

    async def get_server(self, server_id: str) -> ServerDetails | None:
        """Get detailed information about a specific MCP server.

        Args:
            server_id: Unique server identifier in the registry.

        Returns:
            Server details if found, None if not found.

        Raises:
            RegistryError: If the registry request fails.
        """
        ...
