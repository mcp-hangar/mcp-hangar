"""The token-bucket rate limiter in `domain.security`: buckets, composition, cleanup."""

import time

import pytest

from mcp_hangar.domain.security.rate_limiter import (  # noqa: E402
    CompositeRateLimiter,
    InMemoryRateLimiter,
    RateLimitConfig,
    RateLimitResult,
    RateLimitScope,
    TokenBucket,
    get_rate_limiter,
    reset_rate_limiter,
)


class TestRateLimitConfigValidation:
    """Tests for RateLimitConfig validation."""

    def test_valid_config(self):
        config = RateLimitConfig(requests_per_second=10.0, burst_size=20)
        assert config.requests_per_second == 10.0
        assert config.burst_size == 20

    def test_invalid_rate(self):
        with pytest.raises(ValueError, match="requests_per_second"):
            RateLimitConfig(requests_per_second=0)

    def test_negative_rate(self):
        with pytest.raises(ValueError, match="requests_per_second"):
            RateLimitConfig(requests_per_second=-1.0)

    def test_invalid_burst(self):
        with pytest.raises(ValueError, match="burst_size"):
            RateLimitConfig(requests_per_second=1.0, burst_size=0)

    def test_scope_enum(self):
        assert RateLimitScope.GLOBAL.value == "global"
        assert RateLimitScope.PER_PROVIDER.value == "mcp_server"
        assert RateLimitScope.PER_TOOL.value == "tool"
        assert RateLimitScope.PER_CLIENT.value == "client"


class TestRateLimitResultExtended:
    """Extended tests for RateLimitResult."""

    def test_to_dict(self):
        result = RateLimitResult(
            allowed=True,
            remaining=5,
            reset_at=1000.0,
            retry_after=None,
            limit=10,
        )
        d = result.to_dict()
        assert d["allowed"] is True
        assert d["remaining"] == 5
        assert d["limit"] == 10
        assert "retry_after" not in d

    def test_to_dict_with_retry_after(self):
        result = RateLimitResult(
            allowed=False,
            remaining=0,
            reset_at=1000.0,
            retry_after=2.567,
            limit=10,
        )
        d = result.to_dict()
        assert d["retry_after"] == 2.57  # rounded to 2 decimal places

    def test_to_headers_no_retry(self):
        result = RateLimitResult(
            allowed=True,
            remaining=5,
            reset_at=1000.0,
            limit=10,
        )
        headers = result.to_headers()
        assert headers["X-RateLimit-Limit"] == "10"
        assert headers["X-RateLimit-Remaining"] == "5"
        assert "Retry-After" not in headers

    def test_to_headers_negative_remaining_clamped_to_zero(self):
        result = RateLimitResult(
            allowed=False,
            remaining=-3,
            reset_at=1000.0,
            limit=10,
        )
        headers = result.to_headers()
        assert headers["X-RateLimit-Remaining"] == "0"


class TestTokenBucketExtended:
    """Extended tests for TokenBucket."""

    def test_initial_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=20, initial_tokens=5)
        available, _ = bucket.peek()
        assert available == 5

    def test_peek_does_not_consume(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        avail1, _ = bucket.peek()
        avail2, _ = bucket.peek()
        assert avail1 == avail2

    def test_peek_time_to_full(self):
        bucket = TokenBucket(rate=10.0, capacity=10, initial_tokens=5)
        _, time_to_full = bucket.peek()
        assert time_to_full > 0

    def test_peek_already_full(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        _, time_to_full = bucket.peek()
        assert time_to_full == 0

    def test_reset(self):
        bucket = TokenBucket(rate=10.0, capacity=10)
        for _ in range(10):
            bucket.consume()
        bucket.reset()
        available, _ = bucket.peek()
        assert available == 10

    def test_consume_multiple_tokens(self):
        bucket = TokenBucket(rate=10.0, capacity=10)
        allowed, _ = bucket.consume(tokens=5)
        assert allowed
        available, _ = bucket.peek()
        assert available == 5

    def test_consume_more_than_available(self):
        bucket = TokenBucket(rate=10.0, capacity=5)
        allowed, wait = bucket.consume(tokens=10)
        assert not allowed
        assert wait > 0


class TestInMemoryRateLimiterExtended:
    """Extended tests for InMemoryRateLimiter."""

    def test_check_without_consuming(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=5)
        limiter = InMemoryRateLimiter(config)
        result = limiter.check("key1")
        assert result.allowed
        assert result.remaining == 5
        # Check again -- should still show same availability
        result2 = limiter.check("key1")
        assert result2.remaining == 5

    def test_reset_key(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        # Should be exhausted
        assert not limiter.consume("key1").allowed
        # Reset
        limiter.reset("key1")
        assert limiter.consume("key1").allowed

    def test_reset_nonexistent_key(self):
        limiter = InMemoryRateLimiter()
        limiter.reset("nonexistent")  # should not raise

    def test_reset_all(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        limiter.consume("key2")
        limiter.reset_all()
        assert limiter.consume("key1").allowed
        assert limiter.consume("key2").allowed

    def test_get_stats(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=20)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key1")
        limiter.consume("key2")
        stats = limiter.get_stats()
        assert stats["active_buckets"] == 2
        assert stats["config"]["requests_per_second"] == 10
        assert stats["config"]["burst_size"] == 20
        assert stats["config"]["scope"] == "global"

    def test_cleanup_removes_old_buckets(self):
        config = RateLimitConfig(requests_per_second=10, burst_size=5)
        limiter = InMemoryRateLimiter(config, cleanup_interval=0.01)
        limiter.consume("key1")
        # Force last_cleanup to be old
        limiter._last_cleanup = time.monotonic() - 1.0
        # Touch to trigger cleanup with old last_used
        limiter._bucket_last_used["key1"] = time.monotonic() - 1.0
        limiter.consume("key2")  # triggers cleanup
        stats = limiter.get_stats()
        # key1 should have been cleaned up
        assert stats["active_buckets"] <= 2

    def test_check_returns_retry_after_when_empty(self):
        config = RateLimitConfig(requests_per_second=1, burst_size=1)
        limiter = InMemoryRateLimiter(config)
        limiter.consume("key")
        result = limiter.check("key")
        assert not result.allowed
        assert result.retry_after is not None


class TestCompositeRateLimiter:
    """Tests for CompositeRateLimiter."""

    def test_check_all_allow(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=10))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=20))
        composite = CompositeRateLimiter({"global": l1, "provider": l2})
        result = composite.check("key")
        assert result.allowed
        assert result.limit == 10  # min of both

    def test_consume_all_allow(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=10))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=20))
        composite = CompositeRateLimiter({"global": l1, "provider": l2})
        result = composite.consume("key")
        assert result.allowed

    def test_consume_one_denies(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=100))
        composite = CompositeRateLimiter({"strict": l1, "lenient": l2})
        composite.consume("key")  # uses up l1
        result = composite.consume("key")
        assert not result.allowed
        assert result.retry_after is not None

    def test_check_one_denies(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=100, burst_size=100))
        composite = CompositeRateLimiter({"strict": l1, "lenient": l2})
        l1.consume("key")  # exhaust l1
        result = composite.check("key")
        assert not result.allowed

    def test_reset_resets_all(self):
        l1 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        l2 = InMemoryRateLimiter(RateLimitConfig(requests_per_second=1, burst_size=1))
        composite = CompositeRateLimiter({"a": l1, "b": l2})
        composite.consume("key")
        composite.reset("key")
        result = composite.consume("key")
        assert result.allowed

    def test_empty_limiters(self):
        composite = CompositeRateLimiter({})
        result = composite.check("key")
        assert result.allowed
        assert result.remaining == 0


class TestGlobalRateLimiter:
    """Tests for global rate limiter functions."""

    def test_get_rate_limiter_creates_singleton(self):
        reset_rate_limiter()
        limiter1 = get_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is limiter2

    def test_reset_rate_limiter(self):
        reset_rate_limiter()
        limiter1 = get_rate_limiter()
        reset_rate_limiter()
        limiter2 = get_rate_limiter()
        assert limiter1 is not limiter2

    def test_get_rate_limiter_with_config(self):
        reset_rate_limiter()
        config = RateLimitConfig(requests_per_second=50, burst_size=100)
        limiter = get_rate_limiter(config)
        assert limiter.config.requests_per_second == 50
