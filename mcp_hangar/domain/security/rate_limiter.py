"""
Rate limiter for the MCP Registry.

Provides rate limiting to prevent DoS attacks and abuse.
Implements token bucket algorithm for flexible rate control.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
import threading
import time
from typing import Dict, Optional, Tuple


class RateLimitScope(Enum):
    """Scope for rate limiting."""

    GLOBAL = "global"  # Global rate limit
    PER_PROVIDER = "provider"  # Per-provider rate limit
    PER_TOOL = "tool"  # Per-tool rate limit
    PER_CLIENT = "client"  # Per-client rate limit


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    # Token bucket parameters
    requests_per_second: float = 10.0  # Rate at which tokens are added
    burst_size: int = 20  # Maximum tokens (burst capacity)

    # Scope
    scope: RateLimitScope = RateLimitScope.GLOBAL

    # Behavior
    block_on_exceed: bool = True  # Whether to block or just track
    retry_after_header: bool = True  # Whether to include retry-after info

    def __post_init__(self):
        """Validate configuration."""
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be positive")
        if self.burst_size <= 0:
            raise ValueError("burst_size must be positive")


@dataclass
class RateLimitResult:
    """Result of a rate limit check."""

    allowed: bool
    remaining: int  # Remaining requests in window
    reset_at: float  # When the limit resets (timestamp)
    retry_after: Optional[float] = None  # Seconds until request would be allowed
    limit: int = 0  # The configured limit

    def to_dict(self) -> Dict[str, any]:
        """Convert to dictionary for API responses."""
        result = {
            "allowed": self.allowed,
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "limit": self.limit,
        }
        if self.retry_after is not None:
            result["retry_after"] = round(self.retry_after, 2)
        return result

    def to_headers(self) -> Dict[str, str]:
        """Convert to rate limit headers."""
        headers = {
            "X-RateLimit-Limit": str(self.limit),
            "X-RateLimit-Remaining": str(max(0, self.remaining)),
            "X-RateLimit-Reset": str(int(self.reset_at)),
        }
        if self.retry_after is not None and self.retry_after > 0:
            headers["Retry-After"] = str(int(self.retry_after) + 1)
        return headers


class TokenBucket:
    """
    Token bucket implementation for rate limiting.

    Tokens are added at a fixed rate up to a maximum (burst) capacity.
    Each request consumes one token. Requests are allowed if tokens are available.
    """

    def __init__(
        self,
        rate: float,
        capacity: int,
        initial_tokens: Optional[int] = None,
    ):
        """
        Initialize token bucket.

        Args:
            rate: Tokens added per second
            capacity: Maximum tokens (burst size)
            initial_tokens: Starting tokens (defaults to capacity)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(initial_tokens if initial_tokens is not None else capacity)
        self.last_update = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self.last_update
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.last_update = now

    def consume(self, tokens: int = 1) -> Tuple[bool, float]:
        """
        Try to consume tokens.

        Args:
            tokens: Number of tokens to consume

        Returns:
            Tuple of (allowed, wait_time) where wait_time is seconds until
            enough tokens would be available (0 if allowed)
        """
        with self._lock:
            self._refill()

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True, 0.0
            else:
                # Calculate wait time
                needed = tokens - self.tokens
                wait_time = needed / self.rate
                return False, wait_time

    def peek(self) -> Tuple[int, float]:
        """
        Check current state without consuming.

        Returns:
            Tuple of (available_tokens, time_to_full)
        """
        with self._lock:
            self._refill()
            available = int(self.tokens)
            time_to_full = (self.capacity - self.tokens) / self.rate if self.tokens < self.capacity else 0
            return available, time_to_full

    def reset(self) -> None:
        """Reset bucket to full capacity."""
        with self._lock:
            self.tokens = float(self.capacity)
            self.last_update = time.monotonic()


class RateLimiter(ABC):
    """Abstract base class for rate limiters."""

    @abstractmethod
    def check(self, key: str = "global") -> RateLimitResult:
        """
        Check if a request is allowed.

        Args:
            key: Identifier for the rate limit bucket

        Returns:
            RateLimitResult indicating if request is allowed
        """
        pass

    @abstractmethod
    def consume(self, key: str = "global", tokens: int = 1) -> RateLimitResult:
        """
        Consume tokens and return result.

        Args:
            key: Identifier for the rate limit bucket
            tokens: Number of tokens to consume

        Returns:
            RateLimitResult indicating if request was allowed
        """
        pass

    @abstractmethod
    def reset(self, key: str = "global") -> None:
        """Reset rate limit for a key."""
        pass


class InMemoryRateLimiter(RateLimiter):
    """
    In-memory rate limiter using token buckets.

    Suitable for single-instance deployments. For distributed systems,
    use a Redis-backed implementation.
    """

    def __init__(
        self,
        config: Optional[RateLimitConfig] = None,
        cleanup_interval: float = 60.0,
    ):
        """
        Initialize rate limiter.

        Args:
            config: Rate limit configuration
            cleanup_interval: How often to clean up old buckets (seconds)
        """
        self.config = config or RateLimitConfig()
        self.cleanup_interval = cleanup_interval
        self._buckets: Dict[str, TokenBucket] = {}
        self._bucket_last_used: Dict[str, float] = {}
        self._lock = threading.Lock()
        self._last_cleanup = time.monotonic()

    def _get_bucket(self, key: str) -> TokenBucket:
        """Get or create a token bucket for the given key."""
        with self._lock:
            if key not in self._buckets:
                self._buckets[key] = TokenBucket(
                    rate=self.config.requests_per_second,
                    capacity=self.config.burst_size,
                )
            self._bucket_last_used[key] = time.monotonic()

            # Periodic cleanup
            self._maybe_cleanup()

            return self._buckets[key]

    def _maybe_cleanup(self) -> None:
        """Clean up old buckets to prevent memory growth."""
        now = time.monotonic()
        if now - self._last_cleanup < self.cleanup_interval:
            return

        self._last_cleanup = now

        # Remove buckets not used in the last cleanup interval
        cutoff = now - self.cleanup_interval
        keys_to_remove = [key for key, last_used in self._bucket_last_used.items() if last_used < cutoff]

        for key in keys_to_remove:
            self._buckets.pop(key, None)
            self._bucket_last_used.pop(key, None)

    def check(self, key: str = "global") -> RateLimitResult:
        """Check if a request would be allowed without consuming."""
        bucket = self._get_bucket(key)
        available, time_to_full = bucket.peek()

        return RateLimitResult(
            allowed=available > 0,
            remaining=available,
            reset_at=time.time() + time_to_full,
            retry_after=(None if available > 0 else 1.0 / self.config.requests_per_second),
            limit=self.config.burst_size,
        )

    def consume(self, key: str = "global", tokens: int = 1) -> RateLimitResult:
        """Consume tokens and return result."""
        bucket = self._get_bucket(key)
        allowed, wait_time = bucket.consume(tokens)
        available, time_to_full = bucket.peek()

        return RateLimitResult(
            allowed=allowed,
            remaining=available,
            reset_at=time.time() + time_to_full,
            retry_after=wait_time if not allowed else None,
            limit=self.config.burst_size,
        )

    def reset(self, key: str = "global") -> None:
        """Reset rate limit for a key."""
        with self._lock:
            if key in self._buckets:
                self._buckets[key].reset()

    def reset_all(self) -> None:
        """Reset all rate limits."""
        with self._lock:
            self._buckets.clear()
            self._bucket_last_used.clear()

    def get_stats(self) -> Dict[str, any]:
        """Get rate limiter statistics."""
        with self._lock:
            return {
                "active_buckets": len(self._buckets),
                "config": {
                    "requests_per_second": self.config.requests_per_second,
                    "burst_size": self.config.burst_size,
                    "scope": self.config.scope.value,
                },
            }


class CompositeRateLimiter(RateLimiter):
    """
    Composite rate limiter that combines multiple limiters.

    All limiters must allow the request for it to be allowed.
    Useful for implementing both global and per-provider limits.
    """

    def __init__(self, limiters: Dict[str, RateLimiter]):
        """
        Initialize composite limiter.

        Args:
            limiters: Dictionary mapping names to rate limiters
        """
        self.limiters = limiters

    def check(self, key: str = "global") -> RateLimitResult:
        """Check if request would be allowed by all limiters."""
        results = []
        for name, limiter in self.limiters.items():
            result = limiter.check(key)
            results.append((name, result))

        # Find the most restrictive result
        allowed = all(r[1].allowed for r in results)
        min_remaining = min(r[1].remaining for r in results) if results else 0
        max_reset = max(r[1].reset_at for r in results) if results else time.time()
        max_retry = max((r[1].retry_after or 0 for r in results), default=None)

        return RateLimitResult(
            allowed=allowed,
            remaining=min_remaining,
            reset_at=max_reset,
            retry_after=max_retry if not allowed else None,
            limit=min(r[1].limit for r in results) if results else 0,
        )

    def consume(self, key: str = "global", tokens: int = 1) -> RateLimitResult:
        """Consume tokens from all limiters."""
        results = []
        for name, limiter in self.limiters.items():
            result = limiter.consume(key, tokens)
            results.append((name, result))

        # Find the most restrictive result
        allowed = all(r[1].allowed for r in results)
        min_remaining = min(r[1].remaining for r in results) if results else 0
        max_reset = max(r[1].reset_at for r in results) if results else time.time()
        max_retry = max((r[1].retry_after or 0 for r in results), default=None)

        return RateLimitResult(
            allowed=allowed,
            remaining=min_remaining,
            reset_at=max_reset,
            retry_after=max_retry if not allowed else None,
            limit=min(r[1].limit for r in results) if results else 0,
        )

    def reset(self, key: str = "global") -> None:
        """Reset all limiters for a key."""
        for limiter in self.limiters.values():
            limiter.reset(key)


# --- Global rate limiter instance ---

_global_limiter: Optional[InMemoryRateLimiter] = None


def get_rate_limiter(config: Optional[RateLimitConfig] = None) -> InMemoryRateLimiter:
    """Get or create the global rate limiter instance."""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = InMemoryRateLimiter(config)
    return _global_limiter


def reset_rate_limiter() -> None:
    """Reset the global rate limiter (for testing)."""
    global _global_limiter
    _global_limiter = None
