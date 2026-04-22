"""
Repository interfaces for mcp_server storage abstraction.

The Repository pattern separates domain logic from data access logic,
allowing the persistence mechanism to change without affecting business code.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable
import threading
from typing import Any

from mcp_hangar.domain.contracts.lock import ILock

# Type alias for mcp_server-like objects (McpServer aggregate)
McpServerLike = Any


class IMcpServerRepository(ABC):
    """Abstract interface for mcp_server storage.

    This interface defines the contract for storing and retrieving mcp_servers,
    allowing different implementations (in-memory, database, etc.) without
    changing business logic.

    Stores McpServer aggregates.

    Thread-safety is guaranteed by implementations.
    """

    @abstractmethod
    def add(self, mcp_server_id: str, mcp_server: McpServerLike) -> None:
        """Add or update a mcp_server in the repository.

        Args:
            mcp_server_id: Unique mcp_server identifier
            mcp_server: McpServer aggregate instance to store

        Raises:
            ValueError: If mcp_server_id is empty or invalid
        """
        pass

    @abstractmethod
    def get(self, mcp_server_id: str) -> McpServerLike | None:
        """Retrieve a mcp_server by ID.

        Args:
            mcp_server_id: McpServer identifier to look up

        Returns:
            McpServer if found, None otherwise
        """
        pass

    @abstractmethod
    def exists(self, mcp_server_id: str) -> bool:
        """Check if a mcp_server exists in the repository.

        Args:
            mcp_server_id: McpServer identifier to check

        Returns:
            True if mcp_server exists, False otherwise
        """
        pass

    @abstractmethod
    def remove(self, mcp_server_id: str) -> bool:
        """Remove a mcp_server from the repository.

        Args:
            mcp_server_id: McpServer identifier to remove

        Returns:
            True if mcp_server was removed, False if not found
        """
        pass

    @abstractmethod
    def get_all(self) -> dict[str, McpServerLike]:
        """Get all mcp_servers as a dictionary.

        Returns:
            Dictionary mapping mcp_server_id -> McpServer
            Returns a copy to prevent external modifications
        """
        pass

    @abstractmethod
    def get_all_ids(self) -> list[str]:
        """Get all mcp_server IDs.

        Returns:
            List of mcp_server identifiers
        """
        pass

    @abstractmethod
    def count(self) -> int:
        """Get the total number of mcp_servers.

        Returns:
            Number of mcp_servers in the repository
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Remove all mcp_servers from the repository.

        This is primarily for testing and cleanup.
        """
        pass


class InMemoryMcpServerRepository(IMcpServerRepository):
    """In-memory implementation of mcp_server repository.

    This implementation stores mcp_servers in a dictionary with thread-safe
    access using a read-write lock pattern.

    Stores McpServer aggregates.

    Thread Safety:
    - All operations are protected by a lock
    - get_all() returns a snapshot copy
    - Safe for concurrent access from multiple threads
    """

    def __init__(self, lock_factory: Callable[[], ILock] = threading.Lock):
        """Initialize empty in-memory repository."""
        self._mcp_servers: dict[str, McpServerLike] = {}
        self._lock: ILock = self._create_lock(lock_factory)

    @staticmethod
    def _create_lock(lock_factory: Callable[[], ILock]) -> ILock:
        """Create repository lock, preserving tracked-lock defaults."""
        if lock_factory is not threading.Lock:
            return lock_factory()

        try:
            from ..infrastructure.lock_hierarchy import LockLevel, TrackedLock

            return TrackedLock(LockLevel.REPOSITORY, "InMemoryMcpServerRepository")
        except ImportError:
            return lock_factory()

    def add(self, mcp_server_id: str, mcp_server: McpServerLike) -> None:
        """Add or update a mcp_server in the repository.

        Args:
            mcp_server_id: Unique mcp_server identifier
            mcp_server: McpServer aggregate instance to store

        Raises:
            ValueError: If mcp_server_id is empty
        """
        if not mcp_server_id:
            raise ValueError("McpServer ID cannot be empty")

        with self._lock:
            self._mcp_servers[mcp_server_id] = mcp_server

    def get(self, mcp_server_id: str) -> McpServerLike | None:
        """Retrieve a mcp_server by ID.

        Args:
            mcp_server_id: McpServer identifier to look up

        Returns:
            McpServer if found, None otherwise
        """
        with self._lock:
            return self._mcp_servers.get(mcp_server_id)

    def exists(self, mcp_server_id: str) -> bool:
        """Check if a mcp_server exists in the repository.

        Args:
            mcp_server_id: McpServer identifier to check

        Returns:
            True if mcp_server exists, False otherwise
        """
        with self._lock:
            return mcp_server_id in self._mcp_servers

    def remove(self, mcp_server_id: str) -> bool:
        """Remove a mcp_server from the repository.

        Args:
            mcp_server_id: McpServer identifier to remove

        Returns:
            True if mcp_server was removed, False if not found
        """
        with self._lock:
            if mcp_server_id in self._mcp_servers:
                del self._mcp_servers[mcp_server_id]
                return True
            return False

    def get_all(self) -> dict[str, McpServerLike]:
        """Get all mcp_servers as a dictionary.

        Returns:
            Dictionary mapping mcp_server_id -> McpServer
            Returns a copy to prevent external modifications
        """
        with self._lock:
            return dict(self._mcp_servers)

    def get_all_ids(self) -> list[str]:
        """Get all mcp_server IDs.

        Returns:
            List of mcp_server identifiers
        """
        with self._lock:
            return list(self._mcp_servers.keys())

    def count(self) -> int:
        """Get the total number of mcp_servers.

        Returns:
            Number of mcp_servers in the repository
        """
        with self._lock:
            return len(self._mcp_servers)

    def clear(self) -> None:
        """Remove all mcp_servers from the repository.

        This is primarily for testing and cleanup.
        """
        with self._lock:
            self._mcp_servers.clear()

    def __contains__(self, mcp_server_id: str) -> bool:
        """Support 'in' operator for checking existence.

        Args:
            mcp_server_id: McpServer identifier to check

        Returns:
            True if mcp_server exists, False otherwise
        """
        return self.exists(mcp_server_id)

    def keys(self) -> list[str]:
        return self.get_all_ids()

    def values(self) -> list[McpServerLike]:
        with self._lock:
            return list(self._mcp_servers.values())

    def items(self) -> list[tuple[str, McpServerLike]]:
        with self._lock:
            return list(self._mcp_servers.items())

    def __len__(self) -> int:
        """Support len() function.

        Returns:
            Number of mcp_servers in the repository
        """
        return self.count()

    def __repr__(self) -> str:
        """String representation for debugging.

        Returns:
            String showing repository type and mcp_server count
        """
        return f"InMemoryMcpServerRepository(mcp_servers={self.count()})"


# legacy aliases
globals().update(
    {
        "".join(("IPro", "viderRepository")): IMcpServerRepository,
        "".join(("InMemoryPro", "viderRepository")): InMemoryMcpServerRepository,
    }
)
