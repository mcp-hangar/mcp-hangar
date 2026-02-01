"""Redis-backed response cache for distributed deployments.

Provides response caching using Redis for environments where
multiple instances need to share the truncation cache.
"""

import json
from typing import Any

from ...domain.contracts.response_cache import CacheRetrievalResult, IResponseCache
from ...logging_config import get_logger

logger = get_logger(__name__)

# Key prefix for all truncation cache entries
KEY_PREFIX = "mcp:cont:"


class RedisResponseCache(IResponseCache):
    """Redis-backed response cache with automatic TTL.

    Uses Redis for distributed caching with:
    - Automatic TTL via Redis SETEX
    - Atomic operations
    - Offset/limit pagination for large responses

    Requires redis package: pip install redis

    Attributes:
        redis_url: Redis connection URL.
    """

    def __init__(self, redis_url: str):
        """Initialize the Redis cache.

        Args:
            redis_url: Redis connection URL (e.g., redis://localhost:6379).

        Raises:
            ImportError: If redis package is not installed.
            ValueError: If redis_url is empty.
        """
        if not redis_url:
            raise ValueError("redis_url is required")

        try:
            import redis
        except ImportError as e:
            raise ImportError(
                "redis package is required for RedisResponseCache. " "Install with: pip install redis"
            ) from e

        self._redis_url = redis_url
        self._client: redis.Redis = redis.from_url(redis_url, decode_responses=True)

        logger.info("redis_cache_initialized", url=self._sanitize_url(redis_url))

    def _sanitize_url(self, url: str) -> str:
        """Sanitize URL for logging (hide password)."""
        if "@" in url:
            # URL contains credentials
            proto_end = url.find("://") + 3
            at_pos = url.rfind("@")
            return url[:proto_end] + "***:***" + url[at_pos:]
        return url

    def _make_key(self, continuation_id: str) -> str:
        """Create the Redis key for a continuation ID."""
        return f"{KEY_PREFIX}{continuation_id}"

    def store(self, continuation_id: str, full_response: Any, ttl_s: int) -> None:
        """Store a full response in Redis.

        Args:
            continuation_id: Unique identifier for this cached response.
            full_response: The complete response data to cache.
            ttl_s: Time-to-live in seconds.
        """
        if ttl_s <= 0:
            ttl_s = 300  # Default 5 minutes

        try:
            serialized = json.dumps(full_response)
        except (TypeError, ValueError) as e:
            logger.warning(
                "redis_cache_store_serialization_failed",
                continuation_id=continuation_id,
                error=str(e),
            )
            return

        key = self._make_key(continuation_id)

        try:
            self._client.setex(key, ttl_s, serialized)
            logger.debug(
                "redis_cache_entry_stored",
                continuation_id=continuation_id,
                size_bytes=len(serialized),
                ttl_s=ttl_s,
            )
        except Exception as e:
            logger.error(
                "redis_cache_store_failed",
                continuation_id=continuation_id,
                error=str(e),
            )

    def retrieve(
        self,
        continuation_id: str,
        offset: int = 0,
        limit: int | None = None,
    ) -> CacheRetrievalResult:
        """Retrieve a cached response from Redis.

        Args:
            continuation_id: The continuation ID to look up.
            offset: Byte offset to start reading from.
            limit: Maximum bytes to return (None for all remaining).

        Returns:
            CacheRetrievalResult with the response data or not-found status.
        """
        key = self._make_key(continuation_id)

        try:
            serialized = self._client.get(key)
        except Exception as e:
            logger.error(
                "redis_cache_retrieve_failed",
                continuation_id=continuation_id,
                error=str(e),
            )
            return CacheRetrievalResult(found=False)

        if serialized is None:
            return CacheRetrievalResult(found=False)

        # Calculate total size
        serialized_bytes = serialized.encode("utf-8")
        total_size = len(serialized_bytes)

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
        if limit is None:
            chunk = serialized_bytes[offset:]
        else:
            chunk = serialized_bytes[offset : offset + limit]

        has_more = offset + len(chunk) < total_size
        complete = not has_more and offset == 0

        # Try to deserialize if it's the complete response
        if complete:
            try:
                data = json.loads(serialized)
            except json.JSONDecodeError:
                data = serialized
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
        """Delete a cached response from Redis.

        Args:
            continuation_id: The continuation ID to delete.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """
        key = self._make_key(continuation_id)

        try:
            deleted = self._client.delete(key)
            if deleted:
                logger.debug("redis_cache_entry_deleted", continuation_id=continuation_id)
            return deleted > 0
        except Exception as e:
            logger.error(
                "redis_cache_delete_failed",
                continuation_id=continuation_id,
                error=str(e),
            )
            return False

    def clear_expired(self) -> int:
        """Clear expired entries.

        Redis handles TTL automatically, so this is a no-op.

        Returns:
            Always returns 0 as Redis manages expiration.
        """
        # Redis handles TTL automatically
        return 0

    def size(self) -> int:
        """Get approximate number of continuation entries.

        Note: This scans Redis keys matching the prefix, which can be
        slow on large Redis instances. Use sparingly.

        Returns:
            Approximate count of cached continuations.
        """
        try:
            cursor = "0"
            count = 0
            while cursor != 0:
                cursor, keys = self._client.scan(
                    cursor=int(cursor),
                    match=f"{KEY_PREFIX}*",
                    count=1000,
                )
                count += len(keys)
            return count
        except Exception as e:
            logger.error("redis_cache_size_failed", error=str(e))
            return 0

    def clear(self) -> int:
        """Clear all continuation entries.

        Warning: This scans and deletes all matching keys, which can be
        slow on large Redis instances.

        Returns:
            Number of entries cleared.
        """
        try:
            cursor = "0"
            total_deleted = 0
            while cursor != 0:
                cursor, keys = self._client.scan(
                    cursor=int(cursor),
                    match=f"{KEY_PREFIX}*",
                    count=1000,
                )
                if keys:
                    deleted = self._client.delete(*keys)
                    total_deleted += deleted

            logger.info("redis_cache_cleared", count=total_deleted)
            return total_deleted
        except Exception as e:
            logger.error("redis_cache_clear_failed", error=str(e))
            return 0

    def ping(self) -> bool:
        """Check Redis connection health.

        Returns:
            True if Redis is reachable, False otherwise.
        """
        try:
            return self._client.ping()
        except Exception:
            return False
