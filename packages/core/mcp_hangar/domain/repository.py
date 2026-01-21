"""
Repository interfaces for provider storage abstraction.

The Repository pattern separates domain logic from data access logic,
allowing the persistence mechanism to change without affecting business code.
"""

from abc import ABC, abstractmethod
import threading
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from ..infrastructure.lock_hierarchy import TrackedLock

# Type alias for provider-like objects (Provider aggregate)
ProviderLike = Any


class IProviderRepository(ABC):
    """Abstract interface for provider storage.

    This interface defines the contract for storing and retrieving providers,
    allowing different implementations (in-memory, database, etc.) without
    changing business logic.

    Stores Provider aggregates.

    Thread-safety is guaranteed by implementations.
    """

    @abstractmethod
    def add(self, provider_id: str, provider: ProviderLike) -> None:
        """Add or update a provider in the repository.

        Args:
            provider_id: Unique provider identifier
            provider: Provider aggregate instance to store

        Raises:
            ValueError: If provider_id is empty or invalid
        """
        pass

    @abstractmethod
    def get(self, provider_id: str) -> ProviderLike | None:
        """Retrieve a provider by ID.

        Args:
            provider_id: Provider identifier to look up

        Returns:
            Provider if found, None otherwise
        """
        pass

    @abstractmethod
    def exists(self, provider_id: str) -> bool:
        """Check if a provider exists in the repository.

        Args:
            provider_id: Provider identifier to check

        Returns:
            True if provider exists, False otherwise
        """
        pass

    @abstractmethod
    def remove(self, provider_id: str) -> bool:
        """Remove a provider from the repository.

        Args:
            provider_id: Provider identifier to remove

        Returns:
            True if provider was removed, False if not found
        """
        pass

    @abstractmethod
    def get_all(self) -> dict[str, ProviderLike]:
        """Get all providers as a dictionary.

        Returns:
            Dictionary mapping provider_id -> Provider
            Returns a copy to prevent external modifications
        """
        pass

    @abstractmethod
    def get_all_ids(self) -> list[str]:
        """Get all provider IDs.

        Returns:
            List of provider identifiers
        """
        pass

    @abstractmethod
    def count(self) -> int:
        """Get the total number of providers.

        Returns:
            Number of providers in the repository
        """
        pass

    @abstractmethod
    def clear(self) -> None:
        """Remove all providers from the repository.

        This is primarily for testing and cleanup.
        """
        pass


class InMemoryProviderRepository(IProviderRepository):
    """In-memory implementation of provider repository.

    This implementation stores providers in a dictionary with thread-safe
    access using a read-write lock pattern.

    Stores Provider aggregates.

    Thread Safety:
    - All operations are protected by a lock
    - get_all() returns a snapshot copy
    - Safe for concurrent access from multiple threads
    """

    def __init__(self):
        """Initialize empty in-memory repository."""
        self._providers: dict[str, ProviderLike] = {}
        # Lock hierarchy level: REPOSITORY (31)
        # Safe to acquire after: PROVIDER, PROVIDER_GROUP, EVENT_BUS
        # Safe to acquire before: SAGA_MANAGER, STDIO_CLIENT
        self._lock = self._create_lock()

    @staticmethod
    def _create_lock() -> "TrackedLock | threading.RLock":
        """Create lock with hierarchy tracking."""
        try:
            from ..infrastructure.lock_hierarchy import LockLevel, TrackedLock

            return TrackedLock(LockLevel.REPOSITORY, "InMemoryProviderRepository")
        except ImportError:
            return threading.RLock()

    def add(self, provider_id: str, provider: ProviderLike) -> None:
        """Add or update a provider in the repository.

        Args:
            provider_id: Unique provider identifier
            provider: Provider aggregate instance to store

        Raises:
            ValueError: If provider_id is empty
        """
        if not provider_id:
            raise ValueError("Provider ID cannot be empty")

        with self._lock:
            self._providers[provider_id] = provider

    def get(self, provider_id: str) -> ProviderLike | None:
        """Retrieve a provider by ID.

        Args:
            provider_id: Provider identifier to look up

        Returns:
            Provider if found, None otherwise
        """
        with self._lock:
            return self._providers.get(provider_id)

    def exists(self, provider_id: str) -> bool:
        """Check if a provider exists in the repository.

        Args:
            provider_id: Provider identifier to check

        Returns:
            True if provider exists, False otherwise
        """
        with self._lock:
            return provider_id in self._providers

    def remove(self, provider_id: str) -> bool:
        """Remove a provider from the repository.

        Args:
            provider_id: Provider identifier to remove

        Returns:
            True if provider was removed, False if not found
        """
        with self._lock:
            if provider_id in self._providers:
                del self._providers[provider_id]
                return True
            return False

    def get_all(self) -> dict[str, ProviderLike]:
        """Get all providers as a dictionary.

        Returns:
            Dictionary mapping provider_id -> Provider
            Returns a copy to prevent external modifications
        """
        with self._lock:
            return dict(self._providers)

    def get_all_ids(self) -> list[str]:
        """Get all provider IDs.

        Returns:
            List of provider identifiers
        """
        with self._lock:
            return list(self._providers.keys())

    def count(self) -> int:
        """Get the total number of providers.

        Returns:
            Number of providers in the repository
        """
        with self._lock:
            return len(self._providers)

    def clear(self) -> None:
        """Remove all providers from the repository.

        This is primarily for testing and cleanup.
        """
        with self._lock:
            self._providers.clear()

    def __contains__(self, provider_id: str) -> bool:
        """Support 'in' operator for checking existence.

        Args:
            provider_id: Provider identifier to check

        Returns:
            True if provider exists, False otherwise
        """
        return self.exists(provider_id)

    def __len__(self) -> int:
        """Support len() function.

        Returns:
            Number of providers in the repository
        """
        return self.count()

    def __repr__(self) -> str:
        """String representation for debugging.

        Returns:
            String showing repository type and provider count
        """
        return f"InMemoryProviderRepository(providers={self.count()})"
