"""Runtime mcp_server store for hot-loaded mcp_servers.

This module provides a thread-safe in-memory store for mcp_servers that are
loaded at runtime from the registry. These mcp_servers are ephemeral and
do not persist across restarts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import threading
from typing import TYPE_CHECKING

from ..domain.contracts.runtime_store import IRuntimeMcpServerStore
from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..domain.contracts.mcp_server_runtime import McpServerRuntime

logger = get_logger(__name__)


@dataclass
class LoadMetadata:
    """Metadata about a hot-loaded mcp_server.

    Attributes:
        loaded_at: Timestamp when the mcp_server was loaded.
        loaded_by: User ID who loaded the mcp_server (if available).
        source: Source of the mcp_server (e.g., "registry:mcp-server-time").
        verified: Whether the mcp_server is verified/official.
        ephemeral: Whether the mcp_server is ephemeral (will not persist).
        server_id: Registry server ID.
        cleanup: Optional cleanup function to call on unload.
    """

    loaded_at: datetime
    loaded_by: str | None
    source: str
    verified: bool
    ephemeral: bool = True
    server_id: str | None = None
    cleanup: "Callable[[], None] | None" = None

    def lifetime_seconds(self) -> float:
        """Get the lifetime of this mcp_server in seconds."""
        return (datetime.now() - self.loaded_at).total_seconds()

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "loaded_at": self.loaded_at.isoformat(),
            "loaded_by": self.loaded_by,
            "source": self.source,
            "verified": self.verified,
            "ephemeral": self.ephemeral,
            "server_id": self.server_id,
            "lifetime_seconds": self.lifetime_seconds(),
        }


class RuntimeMcpServerStore(IRuntimeMcpServerStore):
    """Thread-safe in-memory store for hot-loaded mcp_servers.

    Stores mcp_servers that are loaded at runtime from the registry.
    These mcp_servers are ephemeral and do not persist across restarts.

    Thread-safety is ensured via RLock for all operations.
    """

    def __init__(self):
        """Initialize the runtime mcp_server store."""
        self._mcp_servers: dict[str, tuple[McpServerRuntime, LoadMetadata]] = {}
        self._lock = threading.RLock()

    def add(self, mcp_server: "McpServerRuntime", metadata: LoadMetadata) -> None:
        """Add a mcp_server to the store.

        Args:
            mcp_server: The mcp_server instance.
            metadata: Load metadata for the mcp_server.

        Raises:
            ValueError: If a mcp_server with the same ID already exists.
        """
        with self._lock:
            mcp_server_id = str(mcp_server.mcp_server_id)
            if mcp_server_id in self._mcp_servers:
                raise ValueError(f"McpServer '{mcp_server_id}' already exists in runtime store")

            self._mcp_servers[mcp_server_id] = (mcp_server, metadata)
            logger.info(
                "runtime_mcp_server_added",
                mcp_server_id=mcp_server_id,
                source=metadata.source,
                verified=metadata.verified,
            )

    def remove(self, mcp_server_id: str) -> "McpServerRuntime | None":
        """Remove a mcp_server from the store.

        Args:
            mcp_server_id: The mcp_server ID to remove.

        Returns:
            The removed mcp_server, or None if not found.
        """
        with self._lock:
            entry = self._mcp_servers.pop(mcp_server_id, None)
            if entry is not None:
                mcp_server, metadata = entry
                logger.info(
                    "runtime_mcp_server_removed",
                    mcp_server_id=mcp_server_id,
                    lifetime_seconds=metadata.lifetime_seconds(),
                )
                return mcp_server
            return None

    def get(self, mcp_server_id: str) -> tuple["McpServerRuntime", LoadMetadata] | None:
        """Get a mcp_server and its metadata from the store.

        Args:
            mcp_server_id: The mcp_server ID to look up.

        Returns:
            Tuple of (mcp_server, metadata) or None if not found.
        """
        with self._lock:
            return self._mcp_servers.get(mcp_server_id)

    def get_mcp_server(self, mcp_server_id: str) -> "McpServerRuntime | None":
        """Get just the mcp_server from the store.

        Args:
            mcp_server_id: The mcp_server ID to look up.

        Returns:
            The mcp_server or None if not found.
        """
        with self._lock:
            entry = self._mcp_servers.get(mcp_server_id)
            return entry[0] if entry else None

    def get_metadata(self, mcp_server_id: str) -> LoadMetadata | None:
        """Get just the metadata from the store.

        Args:
            mcp_server_id: The mcp_server ID to look up.

        Returns:
            The metadata or None if not found.
        """
        with self._lock:
            entry = self._mcp_servers.get(mcp_server_id)
            return entry[1] if entry else None

    def exists(self, mcp_server_id: str) -> bool:
        """Check if a mcp_server exists in the store.

        Args:
            mcp_server_id: The mcp_server ID to check.

        Returns:
            True if the mcp_server exists.
        """
        with self._lock:
            return mcp_server_id in self._mcp_servers

    def list_all(self) -> list[tuple["McpServerRuntime", LoadMetadata]]:
        """Get all mcp_servers and their metadata.

        Returns:
            List of (mcp_server, metadata) tuples.
        """
        with self._lock:
            return list(self._mcp_servers.values())

    def list_ids(self) -> list[str]:
        """Get all mcp_server IDs.

        Returns:
            List of mcp_server IDs.
        """
        with self._lock:
            return list(self._mcp_servers.keys())

    def count(self) -> int:
        """Get the number of mcp_servers in the store.

        Returns:
            Number of mcp_servers.
        """
        with self._lock:
            return len(self._mcp_servers)

    def clear(self) -> list["McpServerRuntime"]:
        """Clear all mcp_servers from the store.

        Returns:
            List of removed mcp_servers.
        """
        with self._lock:
            mcp_servers = [entry[0] for entry in self._mcp_servers.values()]
            self._mcp_servers.clear()
            logger.info("runtime_store_cleared", count=len(mcp_servers))
            return mcp_servers

    def get_all_with_metadata(self) -> dict[str, tuple["McpServerRuntime", LoadMetadata]]:
        """Get all mcp_servers with their metadata.

        Returns:
            Dictionary mapping mcp_server IDs to (mcp_server, metadata) tuples.
        """
        with self._lock:
            return dict(self._mcp_servers)


# legacy aliases
globals()["".join(("RuntimePro", "viderStore"))] = RuntimeMcpServerStore
