"""Runtime provider store for hot-loaded providers.

This module provides a thread-safe in-memory store for providers that are
loaded at runtime from the registry. These providers are ephemeral and
do not persist across restarts.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import threading
from typing import TYPE_CHECKING

from ..logging_config import get_logger

if TYPE_CHECKING:
    from ..domain.contracts.provider_runtime import ProviderRuntime

logger = get_logger(__name__)


@dataclass
class LoadMetadata:
    """Metadata about a hot-loaded provider.

    Attributes:
        loaded_at: Timestamp when the provider was loaded.
        loaded_by: User ID who loaded the provider (if available).
        source: Source of the provider (e.g., "registry:mcp-server-time").
        verified: Whether the provider is verified/official.
        ephemeral: Whether the provider is ephemeral (will not persist).
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
        """Get the lifetime of this provider in seconds."""
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


class RuntimeProviderStore:
    """Thread-safe in-memory store for hot-loaded providers.

    Stores providers that are loaded at runtime from the registry.
    These providers are ephemeral and do not persist across restarts.

    Thread-safety is ensured via RLock for all operations.
    """

    def __init__(self):
        """Initialize the runtime provider store."""
        self._providers: dict[str, tuple[ProviderRuntime, LoadMetadata]] = {}
        self._lock = threading.RLock()

    def add(self, provider: "ProviderRuntime", metadata: LoadMetadata) -> None:
        """Add a provider to the store.

        Args:
            provider: The provider instance.
            metadata: Load metadata for the provider.

        Raises:
            ValueError: If a provider with the same ID already exists.
        """
        with self._lock:
            provider_id = str(provider.provider_id)
            if provider_id in self._providers:
                raise ValueError(f"Provider '{provider_id}' already exists in runtime store")

            self._providers[provider_id] = (provider, metadata)
            logger.info(
                "runtime_provider_added",
                provider_id=provider_id,
                source=metadata.source,
                verified=metadata.verified,
            )

    def remove(self, provider_id: str) -> "ProviderRuntime | None":
        """Remove a provider from the store.

        Args:
            provider_id: The provider ID to remove.

        Returns:
            The removed provider, or None if not found.
        """
        with self._lock:
            entry = self._providers.pop(provider_id, None)
            if entry is not None:
                provider, metadata = entry
                logger.info(
                    "runtime_provider_removed",
                    provider_id=provider_id,
                    lifetime_seconds=metadata.lifetime_seconds(),
                )
                return provider
            return None

    def get(self, provider_id: str) -> tuple["ProviderRuntime", LoadMetadata] | None:
        """Get a provider and its metadata from the store.

        Args:
            provider_id: The provider ID to look up.

        Returns:
            Tuple of (provider, metadata) or None if not found.
        """
        with self._lock:
            return self._providers.get(provider_id)

    def get_provider(self, provider_id: str) -> "ProviderRuntime | None":
        """Get just the provider from the store.

        Args:
            provider_id: The provider ID to look up.

        Returns:
            The provider or None if not found.
        """
        with self._lock:
            entry = self._providers.get(provider_id)
            return entry[0] if entry else None

    def get_metadata(self, provider_id: str) -> LoadMetadata | None:
        """Get just the metadata from the store.

        Args:
            provider_id: The provider ID to look up.

        Returns:
            The metadata or None if not found.
        """
        with self._lock:
            entry = self._providers.get(provider_id)
            return entry[1] if entry else None

    def exists(self, provider_id: str) -> bool:
        """Check if a provider exists in the store.

        Args:
            provider_id: The provider ID to check.

        Returns:
            True if the provider exists.
        """
        with self._lock:
            return provider_id in self._providers

    def list_all(self) -> list[tuple["ProviderRuntime", LoadMetadata]]:
        """Get all providers and their metadata.

        Returns:
            List of (provider, metadata) tuples.
        """
        with self._lock:
            return list(self._providers.values())

    def list_ids(self) -> list[str]:
        """Get all provider IDs.

        Returns:
            List of provider IDs.
        """
        with self._lock:
            return list(self._providers.keys())

    def count(self) -> int:
        """Get the number of providers in the store.

        Returns:
            Number of providers.
        """
        with self._lock:
            return len(self._providers)

    def clear(self) -> list["ProviderRuntime"]:
        """Clear all providers from the store.

        Returns:
            List of removed providers.
        """
        with self._lock:
            providers = [entry[0] for entry in self._providers.values()]
            self._providers.clear()
            logger.info("runtime_store_cleared", count=len(providers))
            return providers

    def get_all_with_metadata(self) -> dict[str, tuple["ProviderRuntime", LoadMetadata]]:
        """Get all providers with their metadata.

        Returns:
            Dictionary mapping provider IDs to (provider, metadata) tuples.
        """
        with self._lock:
            return dict(self._providers)
