"""Health tracking entity for providers."""

import time
from dataclasses import dataclass, field


@dataclass
class HealthTracker:
    """Tracks health metrics for a provider.

    This is a mutable entity (not a value object) that encapsulates
    health-related business logic including:
    - Failure counting and threshold detection
    - Backoff calculation for retry logic
    - Success/failure recording with timestamps

    Note:
        This class is intentionally mutable as it tracks state over time.
        It is not a value object and should not be compared by value.

    Attributes:
        max_consecutive_failures: Threshold for triggering degradation.

    Example:
        >>> tracker = HealthTracker(max_consecutive_failures=3)
        >>> tracker.record_failure()
        >>> tracker.consecutive_failures
        1
    """

    max_consecutive_failures: int = 3
    _consecutive_failures: int = field(default=0, init=False)
    _last_success_at: float | None = field(default=None, init=False)
    _last_failure_at: float | None = field(default=None, init=False)
    _total_invocations: int = field(default=0, init=False)
    _total_failures: int = field(default=0, init=False)

    @property
    def consecutive_failures(self) -> int:
        """Get the current consecutive failure count.

        Returns:
            Number of consecutive failures since last success.
        """
        return self._consecutive_failures

    @property
    def last_success_at(self) -> float | None:
        """Get the timestamp of last successful operation.

        Returns:
            Unix timestamp of last success, or None if never succeeded.
        """
        return self._last_success_at

    @property
    def last_failure_at(self) -> float | None:
        """Get the timestamp of last failed operation.

        Returns:
            Unix timestamp of last failure, or None if never failed.
        """
        return self._last_failure_at

    @property
    def total_invocations(self) -> int:
        """Get the total number of invocations.

        Returns:
            Total count of success + failure invocations.
        """
        return self._total_invocations

    @property
    def total_failures(self) -> int:
        """Get the total number of failures.

        Returns:
            Total count of failed invocations.
        """
        return self._total_failures

    @property
    def success_rate(self) -> float:
        """Calculate the success rate as a percentage.

        Returns:
            Success rate from 0.0 to 1.0. Returns 1.0 if no invocations yet.
        """
        if self._total_invocations == 0:
            return 1.0
        return (self._total_invocations - self._total_failures) / self._total_invocations

    def record_success(self) -> None:
        """Record a successful operation.

        Resets the consecutive failure counter and updates timestamps.
        """
        self._consecutive_failures = 0
        self._last_success_at = time.time()
        self._total_invocations += 1

    def record_failure(self) -> None:
        """Record a failed operation.

        Increments both consecutive and total failure counters.
        """
        self._consecutive_failures += 1
        self._last_failure_at = time.time()
        self._total_failures += 1
        self._total_invocations += 1

    def record_invocation_failure(self) -> None:
        """Record a failed tool invocation.

        Increments total_failures but not consecutive failures.
        Use this for application-level errors that shouldn't trigger degradation.
        """
        self._total_failures += 1
        self._total_invocations += 1

    def should_degrade(self) -> bool:
        """Check if provider should transition to DEGRADED state.

        Returns:
            True when consecutive failures reach the threshold.
        """
        return self._consecutive_failures >= self.max_consecutive_failures

    def can_retry(self) -> bool:
        """Check if enough time has passed for a retry attempt.

        Uses exponential backoff: min(60, 2^consecutive_failures) seconds.

        Returns:
            True if retry is allowed, False if still in backoff period.
        """
        if self._last_failure_at is None:
            return True

        backoff = self._calculate_backoff()
        elapsed = time.time() - self._last_failure_at
        return elapsed >= backoff

    def time_until_retry(self) -> float:
        """Calculate time remaining until retry is allowed.

        Returns:
            Seconds until retry is allowed. Returns 0 if retry is already allowed.
        """
        if self._last_failure_at is None:
            return 0.0

        backoff = self._calculate_backoff()
        elapsed = time.time() - self._last_failure_at
        remaining = backoff - elapsed
        return max(0.0, remaining)

    def _calculate_backoff(self) -> float:
        """Calculate backoff duration based on consecutive failures."""
        return min(60.0, 2**self._consecutive_failures)

    def reset(self) -> None:
        """Reset health tracker to initial state."""
        self._consecutive_failures = 0
        self._last_success_at = None
        self._last_failure_at = None
        self._total_invocations = 0
        self._total_failures = 0

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "consecutive_failures": self._consecutive_failures,
            "last_success_at": self._last_success_at,
            "last_failure_at": self._last_failure_at,
            "total_invocations": self._total_invocations,
            "total_failures": self._total_failures,
            "success_rate": self.success_rate,
            "can_retry": self.can_retry(),
            "time_until_retry": self.time_until_retry(),
        }
