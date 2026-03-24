"""Response cache interface for truncation system.

Defines the contract for caching full responses when truncation occurs,
allowing clients to retrieve the complete content via continuation IDs.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class CacheRetrievalResult:
    """Result of retrieving a cached response.

    Attributes:
        found: Whether the continuation ID was found.
        data: The response data (full or partial based on offset/limit).
        total_size_bytes: Total size of the full cached response.
        offset: Starting offset of the returned data.
        has_more: Whether more data is available after this chunk.
        complete: Whether this retrieval contains the complete response.
    """

    found: bool
    data: Any = None
    total_size_bytes: int = 0
    offset: int = 0
    has_more: bool = False
    complete: bool = False


class IResponseCache(ABC):
    """Interface for response caching in truncation system.

    Implementations must be thread-safe as they may be accessed
    concurrently from multiple batch execution threads.
    """

    @abstractmethod
    def store(self, continuation_id: str, full_response: Any, ttl_s: int) -> None:
        """Store a full response for later retrieval.

        Args:
            continuation_id: Unique identifier for this cached response.
            full_response: The complete response data to cache.
            ttl_s: Time-to-live in seconds before the entry expires.
        """

    @abstractmethod
    def retrieve(
        self,
        continuation_id: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> CacheRetrievalResult:
        """Retrieve a cached response.

        Args:
            continuation_id: The continuation ID to look up.
            offset: Byte offset to start reading from (for pagination).
            limit: Maximum bytes to return (None for all remaining).

        Returns:
            CacheRetrievalResult with the response data or not-found status.
        """

    @abstractmethod
    def delete(self, continuation_id: str) -> bool:
        """Delete a cached response.

        Args:
            continuation_id: The continuation ID to delete.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """

    @abstractmethod
    def clear_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            The number of entries that were removed.
        """


class NullResponseCache(IResponseCache):
    """No-op cache implementation for when truncation is disabled.

    All operations are no-ops or return empty results.
    """

    def store(self, continuation_id: str, full_response: Any, ttl_s: int) -> None:
        """No-op store."""
        pass

    def retrieve(
        self,
        continuation_id: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> CacheRetrievalResult:
        """Always returns not found."""
        return CacheRetrievalResult(found=False)

    def delete(self, continuation_id: str) -> bool:
        """Always returns False."""
        return False

    def clear_expired(self) -> int:
        """Always returns 0."""
        return 0
