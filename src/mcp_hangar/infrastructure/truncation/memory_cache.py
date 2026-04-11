"""In-memory response cache with LRU eviction and TTL.

Thread-safe cache implementation for storing full responses
when truncation occurs, allowing clients to retrieve complete content.
"""

from collections import OrderedDict
from dataclasses import dataclass
import json
import threading
import time
from typing import Any

from ...domain.contracts.response_cache import CacheRetrievalResult, IResponseCache
from ...logging_config import get_logger

logger = get_logger(__name__)


@dataclass
class CacheEntry:
    """Cache entry with value, serialized form, and expiration.

    Attributes:
        value: The original response data.
        serialized: JSON-serialized string of the value.
        expires_at: Unix timestamp when this entry expires.
    """

    value: Any
    serialized: str
    expires_at: float


class MemoryResponseCache(IResponseCache):
    """Thread-safe LRU cache with TTL for response caching.

    Provides in-memory caching with:
    - LRU eviction when capacity is reached
    - TTL-based expiration
    - Offset/limit pagination for large responses

    Attributes:
        max_entries: Maximum number of entries in the cache.
        default_ttl_s: Default time-to-live in seconds.
    """

    def __init__(
        self,
        max_entries: int = 10_000,
        default_ttl_s: int = 300,
    ):
        """Initialize the memory cache.

        Args:
            max_entries: Maximum number of entries (default: 10000).
            default_ttl_s: Default TTL in seconds (default: 300).

        Raises:
            ValueError: If max_entries or default_ttl_s is not positive.
        """
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        if default_ttl_s <= 0:
            raise ValueError("default_ttl_s must be positive")

        self._max_entries = max_entries
        self._default_ttl_s = default_ttl_s
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def max_entries(self) -> int:
        """Get the maximum number of entries."""
        return self._max_entries

    @property
    def default_ttl_s(self) -> int:
        """Get the default TTL in seconds."""
        return self._default_ttl_s

    def store(self, continuation_id: str, full_response: Any, ttl_s: int) -> None:
        """Store a full response in the cache.

        Args:
            continuation_id: Unique identifier for this cached response.
            full_response: The complete response data to cache.
            ttl_s: Time-to-live in seconds (uses default if <= 0).
        """
        if ttl_s <= 0:
            ttl_s = self._default_ttl_s

        try:
            serialized = json.dumps(full_response)
        except (TypeError, ValueError) as e:
            logger.warning(
                "cache_store_serialization_failed",
                continuation_id=continuation_id,
                error=str(e),
            )
            return

        with self._lock:
            # Remove existing entry if present
            if continuation_id in self._cache:
                del self._cache[continuation_id]

            # Evict LRU entries if at capacity
            while len(self._cache) >= self._max_entries:
                evicted_key, _ = self._cache.popitem(last=False)
                logger.debug("cache_entry_evicted", continuation_id=evicted_key)

            # Add new entry
            self._cache[continuation_id] = CacheEntry(
                value=full_response,
                serialized=serialized,
                expires_at=time.time() + ttl_s,
            )

            logger.debug(
                "cache_entry_stored",
                continuation_id=continuation_id,
                size_bytes=len(serialized),
                ttl_s=ttl_s,
            )

    def retrieve(
        self,
        continuation_id: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> CacheRetrievalResult:
        """Retrieve a cached response.

        Args:
            continuation_id: The continuation ID to look up.
            offset: Byte offset to start reading from.
            limit: Maximum bytes to return (None for all remaining).

        Returns:
            CacheRetrievalResult with the response data or not-found status.
        """
        with self._lock:
            entry = self._cache.get(continuation_id)

            if entry is None:
                return CacheRetrievalResult(found=False)

            # Check expiration
            if time.time() > entry.expires_at:
                del self._cache[continuation_id]
                logger.debug("cache_entry_expired", continuation_id=continuation_id)
                return CacheRetrievalResult(found=False)

            # Move to end of LRU order
            self._cache.move_to_end(continuation_id)

            # Get total size
            total_size = len(entry.serialized.encode("utf-8"))

            # Handle offset/limit for byte-level pagination
            if offset >= total_size:
                return CacheRetrievalResult(
                    found=True,
                    data=None,
                    total_size_bytes=total_size,
                    offset=offset,
                    has_more=False,
                    complete=True,
                )

            # Extract the requested portion
            serialized_bytes = entry.serialized.encode("utf-8")
            if limit is None:
                chunk = serialized_bytes[offset:]
            else:
                chunk = serialized_bytes[offset : offset + limit]

            has_more = offset + len(chunk) < total_size
            complete = not has_more and offset == 0

            # Try to deserialize the chunk if it's the complete response
            if complete:
                data = entry.value
            else:
                # Return raw string for partial responses
                data = chunk.decode("utf-8", errors="replace")

            return CacheRetrievalResult(
                found=True,
                data=data,
                total_size_bytes=total_size,
                offset=offset,
                has_more=has_more,
                complete=complete,
            )

    def delete(self, continuation_id: str) -> bool:
        """Delete a cached response.

        Args:
            continuation_id: The continuation ID to delete.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """
        with self._lock:
            if continuation_id in self._cache:
                del self._cache[continuation_id]
                logger.debug("cache_entry_deleted", continuation_id=continuation_id)
                return True
            return False

    def clear_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            The number of entries that were removed.
        """
        with self._lock:
            now = time.time()
            expired_keys = [key for key, entry in self._cache.items() if now > entry.expires_at]
            for key in expired_keys:
                del self._cache[key]

            if expired_keys:
                logger.debug("cache_expired_entries_cleared", count=len(expired_keys))

            return len(expired_keys)

    def size(self) -> int:
        """Get the current number of entries in the cache.

        Returns:
            The number of entries (including expired ones not yet purged).
        """
        with self._lock:
            return len(self._cache)

    def clear(self) -> int:
        """Clear all entries from the cache.

        Returns:
            The number of entries that were cleared.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            logger.info("cache_cleared", count=count)
            return count
