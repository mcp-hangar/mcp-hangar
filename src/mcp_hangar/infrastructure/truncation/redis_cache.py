"""Redis-backed response cache for distributed deployments.

Provides response caching using Redis for environments where
multiple instances need to share the truncation cache.

Each value carries the owner it was stored for, and answers no one else.
See :data:`_OWNER_PREFIX` for the stored form.
"""

import json
from typing import Any, cast

from ...domain.contracts.response_cache import CacheRetrievalResult, IResponseCache
from ...domain.value_objects.truncation import ContinuationOwner, continuation_log_ref
from ...logging_config import get_logger

logger = get_logger(__name__)

# Key prefix for all truncation cache entries
KEY_PREFIX = "mcp:cont:"

#: Marks a value that carries its owner. Such a value is
#: this prefix, the owner as one line of JSON, a newline, and then the payload
#: exactly as it was stored before owners were recorded. ``json.dumps`` never
#: starts its output with ``h`` or emits a raw newline, so the header cannot
#: run into the payload, and a bare payload written by an older replica cannot
#: be mistaken for a value with an owner.
_OWNER_PREFIX = "hangar-continuation-owner:"


def _encode(owner: ContinuationOwner, serialized: str) -> str:
    """The stored form of *serialized* for *owner*."""
    return _OWNER_PREFIX + json.dumps(owner.to_dict(), separators=(",", ":")) + "\n" + serialized


def _decode(stored: str) -> tuple[ContinuationOwner, str] | None:
    """Split a stored value into its owner and payload, or None if it is malformed.

    A value without the prefix was written before owners were recorded: the
    bare payload. It is read as the anonymous owner's. An auth-off gateway
    keeps serving it across the upgrade, no authenticated caller can read it,
    and it expires within ``cache_ttl_s`` like any other entry.
    """
    if not stored.startswith(_OWNER_PREFIX):
        return ContinuationOwner(), stored
    header, newline, payload = stored[len(_OWNER_PREFIX) :].partition("\n")
    if not newline:
        return None
    try:
        return ContinuationOwner.from_dict(json.loads(header)), payload
    except ValueError:  # json.JSONDecodeError is a ValueError
        return None


class RedisResponseCache(IResponseCache):
    """Redis-backed response cache with automatic TTL.

    Uses Redis for distributed caching with:
    - Automatic TTL via Redis SETEX
    - Atomic operations
    - Offset/limit pagination for large responses
    - Values answering only the owner they were stored for

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
                "redis package is required for RedisResponseCache. Install with: pip install redis"
            ) from e

        self._redis_url = redis_url
        self._client: redis.Redis = redis.from_url(redis_url, decode_responses=True)

        # Probe with SETEX, not PING (#1007): a Sentinel listen port answers
        # PING happily and then fails every data command, so a `:26379` typo
        # initialised "successfully" and every store silently failed.
        probe = f"{KEY_PREFIX}__probe__"
        try:
            self._client.setex(probe, 5, "1")
            self._client.delete(probe)
        except Exception as e:  # noqa: BLE001 -- probe must fail closed
            raise RuntimeError(
                f"Redis at {self._sanitize_url(redis_url)} does not accept SETEX "
                "(a Sentinel listen port is not a data node)"
            ) from e

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

    def store(self, continuation_id: str, full_response: Any, ttl_s: int, *, owner: ContinuationOwner) -> bool:
        """Store a full response in Redis.

        Args:
            continuation_id: Unique identifier for this cached response.
            full_response: The complete response data to cache.
            ttl_s: Time-to-live in seconds.
            owner: The caller the value is stored for. It is stored with the
                payload, in the same key, under the same TTL.

        Returns:
            Whether the payload is retrievable under ``continuation_id`` --
            a failed store must not mint a continuation the client cannot
            fetch (#1007).
        """
        if ttl_s <= 0:
            ttl_s = 300  # Default 5 minutes

        try:
            serialized = json.dumps(full_response)
        except (TypeError, ValueError) as e:
            logger.warning(
                "redis_cache_store_serialization_failed",
                continuation_ref=continuation_log_ref(continuation_id),
                error=str(e),
            )
            return False

        key = self._make_key(continuation_id)

        try:
            self._client.setex(key, ttl_s, _encode(owner, serialized))
            logger.debug(
                "redis_cache_entry_stored",
                continuation_ref=continuation_log_ref(continuation_id),
                size_bytes=len(serialized),
                ttl_s=ttl_s,
            )
            return True
        except Exception as e:  # noqa: BLE001 -- infra-boundary: store failure must not mint a continuation_id
            logger.error(
                "redis_cache_store_failed",
                continuation_ref=continuation_log_ref(continuation_id),
                error=str(e),
            )
            return False

    def _owned_payload(self, continuation_id: str, owner: ContinuationOwner, op: str) -> str | None:
        """The payload under *continuation_id* if *owner* stored it, else None.

        A missing key, a Redis failure, a malformed value and another owner's
        value all come back None, so the caller cannot tell them apart.
        """
        ref = continuation_log_ref(continuation_id)
        try:
            stored = self._client.get(self._make_key(continuation_id))
        except Exception as e:  # noqa: BLE001 -- infra-boundary: graceful degradation on Redis failure
            logger.error("redis_cache_retrieve_failed", op=op, continuation_ref=ref, error=str(e))
            return None

        if stored is None:
            return None

        decoded = _decode(stored)
        if decoded is None:
            logger.warning("redis_cache_entry_malformed", op=op, continuation_ref=ref)
            return None

        entry_owner, payload = decoded
        if not entry_owner.admits(owner):
            logger.warning(
                "continuation_owner_mismatch",
                op=op,
                continuation_ref=ref,
                owner_tenant=entry_owner.tenant_id,
                caller_tenant=owner.tenant_id,
            )
            return None

        return payload

    def retrieve(
        self,
        continuation_id: str,
        offset: int = 0,
        limit: int | None = None,
        *,
        owner: ContinuationOwner,
    ) -> CacheRetrievalResult:
        """Retrieve a cached response from Redis.

        Args:
            continuation_id: The continuation ID to look up.
            offset: Byte offset to start reading from.
            limit: Maximum bytes to return (None for all remaining).
            owner: The caller asking. Another owner's value is not found.

        Returns:
            CacheRetrievalResult with the response data or not-found status.
        """
        serialized = self._owned_payload(continuation_id, owner, "retrieve")
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

    def delete(self, continuation_id: str, *, owner: ContinuationOwner) -> bool:
        """Delete a cached response from Redis.

        The owner is read before the key is deleted. Ids are never reused for
        another owner, so nothing can change hands between the two commands.

        Args:
            continuation_id: The continuation ID to delete.
            owner: The caller asking. Another owner's value is left in place.

        Returns:
            True if the entry was deleted, False if it didn't exist.
        """
        if self._owned_payload(continuation_id, owner, "delete") is None:
            return False

        key = self._make_key(continuation_id)

        try:
            deleted = self._client.delete(key)
            if deleted:
                logger.debug("redis_cache_entry_deleted", continuation_ref=continuation_log_ref(continuation_id))
            return cast(bool, deleted > 0)
        except Exception as e:  # noqa: BLE001 -- infra-boundary: graceful degradation on Redis failure
            logger.error(
                "redis_cache_delete_failed",
                continuation_ref=continuation_log_ref(continuation_id),
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
        except Exception as e:  # noqa: BLE001 -- infra-boundary: graceful degradation on Redis failure
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
        except Exception as e:  # noqa: BLE001 -- infra-boundary: graceful degradation on Redis failure
            logger.error("redis_cache_clear_failed", error=str(e))
            return 0

    def ping(self) -> bool:
        """Check Redis connection health.

        Returns:
            True if Redis is reachable, False otherwise.
        """
        try:
            return cast(bool, self._client.ping())
        except Exception:  # noqa: BLE001 -- infra-boundary: graceful degradation on Redis failure
            return False
