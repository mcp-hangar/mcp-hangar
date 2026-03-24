"""LRU cache with TTL for registry responses.

This module provides a thread-safe LRU cache with time-to-live expiration
for caching registry API responses.
"""

from collections import OrderedDict
from dataclasses import dataclass
import threading
import time
from typing import Any


@dataclass
class CacheEntry:
    """Cache entry with value and expiration time.

    Attributes:
        value: The cached value.
        expires_at: Unix timestamp when this entry expires.
    """

    value: Any
    expires_at: float


class RegistryCache:
    """Thread-safe LRU cache with TTL for registry responses.

    Provides a caching layer for registry API responses with configurable
    TTL and maximum entries. Uses LRU eviction when capacity is reached.

    Attributes:
        ttl_seconds: Time-to-live for cache entries in seconds.
        max_entries: Maximum number of entries in the cache.
    """

    DEFAULT_TTL_SECONDS = 86400  # 24 hours
    DEFAULT_MAX_ENTRIES = 1000

    def __init__(
        self,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ):
        """Initialize the cache.

        Args:
            ttl_seconds: Time-to-live for entries in seconds (default: 24h).
            max_entries: Maximum number of entries (default: 1000).

        Raises:
            ValueError: If ttl_seconds or max_entries is not positive.
        """
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")

        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._cache: OrderedDict[str, CacheEntry] = OrderedDict()
        self._lock = threading.RLock()

    @property
    def ttl_seconds(self) -> int:
        """Get the TTL in seconds."""
        return self._ttl_seconds

    @property
    def max_entries(self) -> int:
        """Get the maximum number of entries."""
        return self._max_entries

    def get(self, key: str) -> Any | None:
        """Get a value from the cache.

        If the entry exists and has not expired, returns the value and moves
        the entry to the end of the LRU order. Expired entries are removed.

        Args:
            key: The cache key.

        Returns:
            The cached value if found and not expired, None otherwise.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None

            if time.time() > entry.expires_at:
                del self._cache[key]
                return None

            self._cache.move_to_end(key)
            return entry.value

    def set(self, key: str, value: Any) -> None:
        """Set a value in the cache.

        If the cache is at capacity, removes the least recently used entry
        before adding the new one.

        Args:
            key: The cache key.
            value: The value to cache.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]

            while len(self._cache) >= self._max_entries:
                self._cache.popitem(last=False)

            self._cache[key] = CacheEntry(
                value=value,
                expires_at=time.time() + self._ttl_seconds,
            )

    def delete(self, key: str) -> bool:
        """Delete an entry from the cache.

        Args:
            key: The cache key.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """
        with self._lock:
            if key in self._cache:
                del self._cache[key]
                return True
            return False

    def clear(self) -> int:
        """Clear all entries from the cache.

        Returns:
            The number of entries that were cleared.
        """
        with self._lock:
            count = len(self._cache)
            self._cache.clear()
            return count

    def purge_expired(self) -> int:
        """Remove all expired entries from the cache.

        Returns:
            The number of entries that were purged.
        """
        with self._lock:
            now = time.time()
            expired_keys = [key for key, entry in self._cache.items() if now > entry.expires_at]
            for key in expired_keys:
                del self._cache[key]
            return len(expired_keys)

    def size(self) -> int:
        """Get the current number of entries in the cache.

        Returns:
            The number of entries (including expired ones not yet purged).
        """
        with self._lock:
            return len(self._cache)

    def contains(self, key: str) -> bool:
        """Check if a key exists in the cache and is not expired.

        Args:
            key: The cache key.

        Returns:
            True if the key exists and has not expired.
        """
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return False
            if time.time() > entry.expires_at:
                del self._cache[key]
                return False
            return True
