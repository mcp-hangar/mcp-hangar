"""Rate limiting for authentication attempts.

Provides protection against brute-force attacks by limiting
the number of failed authentication attempts per IP address.

Uses a token bucket algorithm with per-IP tracking.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time
from typing import NamedTuple

import structlog

from ...domain.events import RateLimitLockout, RateLimitUnlock

logger = structlog.get_logger(__name__)


class RateLimitResult(NamedTuple):
    """Result of a rate limit check."""

    allowed: bool
    remaining: int
    retry_after: float | None  # Seconds until next attempt allowed
    reason: str


@dataclass
class AuthRateLimitConfig:
    """Configuration for authentication rate limiting.

    Attributes:
        enabled: Whether rate limiting is enabled.
        max_attempts: Maximum failed attempts per window.
        window_seconds: Time window for counting attempts.
        lockout_seconds: How long to lock out after exceeding limit.
        cleanup_interval: How often to clean up old entries.
        lockout_escalation_factor: Multiplier for each consecutive lockout.
        max_lockout_seconds: Maximum lockout duration (cap for exponential backoff).
    """

    enabled: bool = True
    max_attempts: int = 10
    window_seconds: int = 60
    lockout_seconds: int = 300
    cleanup_interval: int = 300  # 5 minutes
    lockout_escalation_factor: float = 2.0
    max_lockout_seconds: int = 3600  # 1 hour


@dataclass
class _AttemptTracker:
    """Tracks authentication attempts for a single IP."""

    attempts: list[float] = field(default_factory=list)
    locked_until: float | None = None
    lockout_count: int = 0


class AuthRateLimiter:
    """Rate limiter for authentication attempts.

    Tracks failed authentication attempts per IP address and
    blocks IPs that exceed the configured threshold.

    Thread-safe implementation using RLock.

    Usage:
        limiter = AuthRateLimiter(config)

        # Before authentication
        result = limiter.check_rate_limit(client_ip)
        if not result.allowed:
            raise RateLimitExceeded(result.retry_after)

        # After failed authentication
        limiter.record_failure(client_ip)

        # After successful authentication
        limiter.record_success(client_ip)
    """

    def __init__(
        self,
        config: AuthRateLimitConfig | None = None,
        event_publisher: Callable[[object], None] | None = None,
    ):
        """Initialize rate limiter.

        Args:
            config: Rate limit configuration. Uses defaults if None.
            event_publisher: Optional callback for publishing domain events.
        """
        self._config = config or AuthRateLimitConfig()
        self._trackers: dict[str, _AttemptTracker] = {}
        self._lock = threading.RLock()
        self._last_cleanup = time.time()
        self._event_publisher = event_publisher

    def _publish_event(self, event: object) -> None:
        """Publish a domain event if event_publisher is configured.

        Args:
            event: Domain event to publish.
        """
        if self._event_publisher:
            try:
                self._event_publisher(event)
            except Exception as e:  # noqa: BLE001 -- infra-boundary: event publishing must not break rate limiting
                logger.warning("rate_limiter_event_publish_failed", event_type=type(event).__name__, error=str(e))

    @property
    def enabled(self) -> bool:
        """Check if rate limiting is enabled."""
        return self._config.enabled

    def check_rate_limit(self, ip: str) -> RateLimitResult:
        """Check if an IP is allowed to attempt authentication.

        Args:
            ip: Client IP address.

        Returns:
            RateLimitResult indicating if attempt is allowed.
        """
        if not self._config.enabled:
            return RateLimitResult(
                allowed=True,
                remaining=self._config.max_attempts,
                retry_after=None,
                reason="rate_limiting_disabled",
            )

        now = time.time()

        with self._lock:
            self._maybe_cleanup(now)

            tracker = self._trackers.get(ip)
            if tracker is None:
                return RateLimitResult(
                    allowed=True,
                    remaining=self._config.max_attempts,
                    retry_after=None,
                    reason="no_previous_attempts",
                )

            # Check if locked out
            if tracker.locked_until is not None:
                if now < tracker.locked_until:
                    retry_after = tracker.locked_until - now
                    logger.warning(
                        "auth_rate_limit_locked",
                        ip=ip,
                        retry_after=retry_after,
                    )
                    return RateLimitResult(
                        allowed=False,
                        remaining=0,
                        retry_after=retry_after,
                        reason="locked_out",
                    )
                else:
                    # Lockout expired, clear lockout flag
                    # Keep lockout_count for exponential backoff
                    # Attempts will be pruned by window logic below
                    self._publish_event(
                        RateLimitUnlock(
                            source_ip=ip,
                            lockout_count=tracker.lockout_count,
                            unlock_reason="expired",
                        )
                    )
                    tracker.locked_until = None

            # Count attempts in current window
            window_start = now - self._config.window_seconds
            recent_attempts = [t for t in tracker.attempts if t > window_start]
            tracker.attempts = recent_attempts  # Prune old attempts

            remaining = self._config.max_attempts - len(recent_attempts)

            if remaining <= 0:
                # Lock out the IP with exponential backoff
                tracker.lockout_count += 1
                effective_lockout = min(
                    self._config.lockout_seconds
                    * (self._config.lockout_escalation_factor ** (tracker.lockout_count - 1)),
                    self._config.max_lockout_seconds,
                )
                tracker.locked_until = now + effective_lockout
                logger.warning(
                    "auth_rate_limit_exceeded",
                    ip=ip,
                    attempts=len(recent_attempts),
                    lockout_seconds=effective_lockout,
                    lockout_count=tracker.lockout_count,
                )
                self._publish_event(
                    RateLimitLockout(
                        source_ip=ip,
                        lockout_duration_seconds=effective_lockout,
                        lockout_count=tracker.lockout_count,
                        failed_attempts=len(recent_attempts),
                    )
                )
                return RateLimitResult(
                    allowed=False,
                    remaining=0,
                    retry_after=effective_lockout,
                    reason="rate_limit_exceeded",
                )

            return RateLimitResult(
                allowed=True,
                remaining=remaining,
                retry_after=None,
                reason="within_limit",
            )

    def record_failure(self, ip: str) -> None:
        """Record a failed authentication attempt.

        Args:
            ip: Client IP address.
        """
        if not self._config.enabled:
            return

        now = time.time()

        with self._lock:
            if ip not in self._trackers:
                self._trackers[ip] = _AttemptTracker()

            tracker = self._trackers[ip]
            tracker.attempts.append(now)

            logger.debug(
                "auth_failure_recorded",
                ip=ip,
                total_attempts=len(tracker.attempts),
            )

    def record_success(self, ip: str) -> None:
        """Record a successful authentication (clears failure count).

        Args:
            ip: Client IP address.
        """
        if not self._config.enabled:
            return

        with self._lock:
            if ip in self._trackers:
                tracker = self._trackers[ip]
                if tracker.locked_until is not None:
                    self._publish_event(
                        RateLimitUnlock(
                            source_ip=ip,
                            lockout_count=tracker.lockout_count,
                            unlock_reason="success",
                        )
                    )
                del self._trackers[ip]
                logger.debug("auth_success_cleared_tracker", ip=ip)

    def get_status(self, ip: str) -> dict:
        """Get rate limit status for an IP.

        Args:
            ip: Client IP address.

        Returns:
            Dict with rate limit status information.
        """
        with self._lock:
            tracker = self._trackers.get(ip)
            if tracker is None:
                return {
                    "ip": ip,
                    "attempts": 0,
                    "remaining": self._config.max_attempts,
                    "locked": False,
                    "locked_until": None,
                }

            now = time.time()
            window_start = now - self._config.window_seconds
            recent = len([t for t in tracker.attempts if t > window_start])

            return {
                "ip": ip,
                "attempts": recent,
                "remaining": max(0, self._config.max_attempts - recent),
                "locked": tracker.locked_until is not None and now < tracker.locked_until,
                "locked_until": tracker.locked_until,
            }

    def clear(self, ip: str | None = None) -> None:
        """Clear rate limit data.

        Args:
            ip: Specific IP to clear, or None to clear all.
        """
        with self._lock:
            if ip is None:
                # Clear all - emit unlock events for locked IPs
                for tracked_ip, tracker in list(self._trackers.items()):
                    if tracker.locked_until is not None:
                        self._publish_event(
                            RateLimitUnlock(
                                source_ip=tracked_ip,
                                lockout_count=tracker.lockout_count,
                                unlock_reason="manual_clear",
                            )
                        )
                self._trackers.clear()
                logger.info("auth_rate_limit_cleared_all")
            elif ip in self._trackers:
                tracker = self._trackers[ip]
                if tracker.locked_until is not None:
                    self._publish_event(
                        RateLimitUnlock(
                            source_ip=ip,
                            lockout_count=tracker.lockout_count,
                            unlock_reason="manual_clear",
                        )
                    )
                del self._trackers[ip]
                logger.info("auth_rate_limit_cleared", ip=ip)

    def _maybe_cleanup(self, now: float) -> None:
        """Clean up old entries periodically.

        Called under lock.
        """
        if now - self._last_cleanup < self._config.cleanup_interval:
            return
        self._do_cleanup(now)

    def _do_cleanup(self, now: float) -> int:
        """Perform cleanup. Must be called under self._lock.

        Returns:
            Number of trackers removed.
        """
        self._last_cleanup = now
        window_start = now - self._config.window_seconds

        # Remove trackers with no recent activity and not locked
        to_remove = []
        for ip, tracker in self._trackers.items():
            # Keep if locked and lockout not expired
            if tracker.locked_until is not None and now < tracker.locked_until:
                continue
            # Keep if has recent attempts
            if any(t > window_start for t in tracker.attempts):
                continue
            # Emit unlock for expired lockouts being cleaned up
            if tracker.locked_until is not None and now >= tracker.locked_until:
                self._publish_event(
                    RateLimitUnlock(
                        source_ip=ip,
                        lockout_count=tracker.lockout_count,
                        unlock_reason="cleanup",
                    )
                )
            to_remove.append(ip)

        for ip in to_remove:
            del self._trackers[ip]

        if to_remove:
            logger.debug("auth_rate_limit_cleanup", removed_count=len(to_remove))

        return len(to_remove)

    def force_cleanup(self) -> int:
        """Force immediate cleanup of stale trackers.

        Returns:
            Number of trackers removed.
        """
        now = time.time()
        with self._lock:
            return self._do_cleanup(now)


# Global instance for use across the application
_default_limiter: AuthRateLimiter | None = None


def get_auth_rate_limiter() -> AuthRateLimiter:
    """Get the global auth rate limiter instance.

    Returns:
        AuthRateLimiter instance.
    """
    global _default_limiter
    if _default_limiter is None:
        _default_limiter = AuthRateLimiter()
    return _default_limiter


def set_auth_rate_limiter(limiter: AuthRateLimiter) -> None:
    """Set the global auth rate limiter instance.

    Args:
        limiter: AuthRateLimiter to use globally.
    """
    global _default_limiter
    _default_limiter = limiter
