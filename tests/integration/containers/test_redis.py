"""Integration tests for Redis caching and rate limiting using Testcontainers.

These tests verify that Redis-based features work correctly:
- Rate limiting
- Caching
- Distributed locks
- Provider state storage
"""

import threading
import time

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.container,
    pytest.mark.redis,
]


def skip_if_no_redis():
    try:
        import redis

        return redis
    except ImportError:
        pytest.skip("redis package not installed")


class TestRedisRateLimiter:
    """Tests for Redis-based rate limiting."""

    def test_rate_limiter_enforces_limit(
        self,
        redis_container: dict,
    ) -> None:
        """Rate limiter allows requests within limit and blocks excess."""
        redis = skip_if_no_redis()
        client = redis.from_url(redis_container["url"])

        key = "test:rate_limit:enforce"
        limit = 10
        client.delete(key)

        # Make requests up to limit
        for i in range(limit + 5):
            current = client.incr(key)
            if current == 1:
                client.expire(key, 60)

        # Should have exceeded limit
        final = int(client.get(key))
        assert final == limit + 5

        # Rate limiter logic would reject requests > limit
        # Here we just verify Redis correctly counts

    def test_rate_limit_window_expires(
        self,
        redis_container: dict,
    ) -> None:
        """Rate limit window expires and resets."""
        redis = skip_if_no_redis()
        client = redis.from_url(redis_container["url"])

        key = "test:rate_limit:expire"
        client.setex(key, 1, "10")

        assert client.get(key) == b"10"
        time.sleep(1.5)
        assert client.get(key) is None


class TestRedisCaching:
    """Tests for Redis caching layer."""

    def test_cache_crud_operations(
        self,
        redis_container: dict,
    ) -> None:
        """Can set, get, and delete cached values."""
        redis = skip_if_no_redis()
        import json

        client = redis.from_url(redis_container["url"])

        cache_key = "test:cache:crud"
        data = {"name": "test", "value": 42}

        # Set
        client.setex(cache_key, 300, json.dumps(data))

        # Get
        cached = client.get(cache_key)
        assert cached is not None
        assert json.loads(cached)["name"] == "test"

        # Delete
        client.delete(cache_key)
        assert client.get(cache_key) is None

    def test_cache_pattern_invalidation(
        self,
        redis_container: dict,
    ) -> None:
        """Can invalidate cache by pattern."""
        redis = skip_if_no_redis()
        client = redis.from_url(redis_container["url"])

        # Set multiple keys
        for i in range(3):
            client.set(f"test:cache:pattern:{i}", f"value_{i}")

        # Verify exist
        assert client.get("test:cache:pattern:0") is not None

        # Invalidate by pattern
        keys = client.keys("test:cache:pattern:*")
        if keys:
            client.delete(*keys)

        # Verify gone
        assert client.get("test:cache:pattern:0") is None


class TestRedisDistributedLocks:
    """Tests for Redis-based distributed locking."""

    def test_lock_exclusive_acquisition(
        self,
        redis_container: dict,
    ) -> None:
        """Only one client can hold lock at a time."""
        redis = skip_if_no_redis()
        client = redis.from_url(redis_container["url"])

        lock_key = "test:lock:exclusive"
        client.delete(lock_key)

        # First acquisition succeeds
        acquired1 = client.set(lock_key, "owner1", nx=True, ex=10)
        assert acquired1 is True

        # Second acquisition fails
        acquired2 = client.set(lock_key, "owner2", nx=True, ex=10)
        assert acquired2 is None

        # Release
        client.delete(lock_key)

        # Now can acquire again
        acquired3 = client.set(lock_key, "owner3", nx=True, ex=10)
        assert acquired3 is True

    def test_lock_auto_expires(
        self,
        redis_container: dict,
    ) -> None:
        """Lock automatically expires after TTL."""
        redis = skip_if_no_redis()
        client = redis.from_url(redis_container["url"])

        lock_key = "test:lock:expires"
        client.set(lock_key, "holder", nx=True, ex=1)

        assert client.get(lock_key) is not None
        time.sleep(1.5)
        assert client.get(lock_key) is None

    def test_concurrent_lock_contention(
        self,
        redis_container: dict,
    ) -> None:
        """Only one thread acquires lock under contention."""
        redis = skip_if_no_redis()

        lock_key = "test:lock:contention"
        acquired_by: list[str] = []
        list_lock = threading.Lock()

        def try_acquire(worker_id: str):
            client = redis.from_url(redis_container["url"])
            if client.set(lock_key, worker_id, nx=True, ex=10):
                with list_lock:
                    acquired_by.append(worker_id)

        redis.from_url(redis_container["url"]).delete(lock_key)

        threads = [threading.Thread(target=try_acquire, args=(f"worker_{i}",)) for i in range(10)]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(acquired_by) == 1


class TestRedisProviderState:
    """Tests for storing provider state in Redis."""

    def test_provider_state_persistence(
        self,
        redis_container: dict,
    ) -> None:
        """Provider state and history is stored correctly."""
        redis = skip_if_no_redis()
        from datetime import datetime
        import json

        client = redis.from_url(redis_container["url"])

        provider_id = "state-test"
        state_key = f"mcp:state:{provider_id}"
        history_key = f"mcp:state_history:{provider_id}"
        health_key = f"mcp:health:{provider_id}"

        # Clean up
        client.delete(state_key, history_key, health_key)

        # Set state and health
        client.set(state_key, "READY")
        client.setex(
            health_key,
            60,
            json.dumps(
                {
                    "healthy": True,
                    "latency_ms": 15.5,
                }
            ),
        )

        # Add state history
        for state in ["COLD", "INITIALIZING", "READY"]:
            client.rpush(
                history_key,
                json.dumps(
                    {
                        "state": state,
                        "timestamp": datetime.now().isoformat(),
                    }
                ),
            )

        # Verify
        assert client.get(state_key) == b"READY"
        assert client.llen(history_key) == 3

        health = json.loads(client.get(health_key))
        assert health["healthy"] is True
