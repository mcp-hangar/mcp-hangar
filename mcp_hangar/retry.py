"""Automatic retry with exponential backoff.

This module provides retry functionality for transient failures,
including:

- Configurable retry policies
- Exponential, linear, and constant backoff strategies
- Per-provider retry configuration
- Circuit breaker integration

Usage example::

    from mcp_hangar import RetryPolicy, BackoffStrategy, with_retry

    policy = RetryPolicy(
        max_attempts=3,
        backoff=BackoffStrategy.EXPONENTIAL
    )

    @with_retry(policy)
    def call_provider():
        return risky_operation()

See docs/guides/UX_IMPROVEMENTS.md for more examples.
"""

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar

from .errors import is_retryable
from .logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")


class BackoffStrategy(str, Enum):
    """Backoff strategy for retries."""

    EXPONENTIAL = "exponential"
    LINEAR = "linear"
    CONSTANT = "constant"


@dataclass
class RetryPolicy:
    """Configuration for automatic retry behavior.

    Attributes:
        max_attempts: Maximum number of attempts (including initial)
        backoff: Backoff strategy (exponential, linear, constant)
        initial_delay: Initial delay in seconds before first retry
        max_delay: Maximum delay cap in seconds
        retry_on: List of error types to retry on
        jitter: Whether to add random jitter to delays
        jitter_factor: Jitter factor (0.0 to 1.0)
    """

    max_attempts: int = 3
    backoff: BackoffStrategy = BackoffStrategy.EXPONENTIAL
    initial_delay: float = 1.0
    max_delay: float = 30.0
    retry_on: List[str] = field(default_factory=lambda: [
        "MalformedJSON",
        "JSONDecodeError",
        "Timeout",
        "TimeoutError",
        "ConnectionError",
        "ProviderNotResponding",
        "TransientError",
        "ProviderProtocolError",
        "NetworkError",
    ])
    jitter: bool = True
    jitter_factor: float = 0.25

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "RetryPolicy":
        """Create RetryPolicy from dictionary (e.g., from config.yaml)."""
        backoff = data.get("backoff", "exponential")
        if isinstance(backoff, str):
            backoff = BackoffStrategy(backoff)

        default_retry_on = [
            "MalformedJSON",
            "JSONDecodeError",
            "Timeout",
            "TimeoutError",
            "ConnectionError",
            "ProviderNotResponding",
            "TransientError",
            "ProviderProtocolError",
            "NetworkError",
        ]

        return cls(
            max_attempts=data.get("max_attempts", 3),
            backoff=backoff,
            initial_delay=data.get("initial_delay", 1.0),
            max_delay=data.get("max_delay", 30.0),
            retry_on=data.get("retry_on", default_retry_on),
            jitter=data.get("jitter", True),
            jitter_factor=data.get("jitter_factor", 0.25),
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "max_attempts": self.max_attempts,
            "backoff": self.backoff.value if isinstance(self.backoff, BackoffStrategy) else self.backoff,
            "initial_delay": self.initial_delay,
            "max_delay": self.max_delay,
            "retry_on": self.retry_on,
            "jitter": self.jitter,
            "jitter_factor": self.jitter_factor,
        }


@dataclass
class RetryAttempt:
    """Record of a single retry attempt."""

    attempt_number: int
    error_type: str
    error_message: str
    delay_before: float
    timestamp: float = field(default_factory=time.time)


@dataclass
class RetryResult:
    """Result of a retry operation."""

    success: bool
    result: Any = None
    final_error: Optional[Exception] = None
    attempts: List[RetryAttempt] = field(default_factory=list)
    total_time_s: float = 0.0

    @property
    def attempt_count(self) -> int:
        """Total number of attempts made."""
        return len(self.attempts) + (1 if self.success else 0)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for logging/reporting."""
        return {
            "success": self.success,
            "attempt_count": self.attempt_count,
            "total_time_s": self.total_time_s,
            "attempts": [
                {
                    "attempt": a.attempt_number,
                    "error_type": a.error_type,
                    "error_message": a.error_message[:100],
                    "delay_before": a.delay_before,
                }
                for a in self.attempts
            ],
            "final_error": str(self.final_error) if self.final_error else None,
        }


def calculate_backoff(
    attempt: int,
    strategy: BackoffStrategy,
    initial_delay: float,
    max_delay: float,
    jitter: bool = True,
    jitter_factor: float = 0.25,
) -> float:
    """Calculate delay before next retry.

    Args:
        attempt: Current attempt number (0-indexed)
        strategy: Backoff strategy
        initial_delay: Base delay in seconds
        max_delay: Maximum delay cap
        jitter: Whether to add random jitter
        jitter_factor: Jitter range (e.g., 0.25 = ±25%)

    Returns:
        Delay in seconds
    """
    if strategy == BackoffStrategy.EXPONENTIAL:
        # min(initial_delay * 2^attempt, max_delay)
        delay = min(initial_delay * (2 ** attempt), max_delay)
    elif strategy == BackoffStrategy.LINEAR:
        # initial_delay * (attempt + 1), capped at max_delay
        delay = min(initial_delay * (attempt + 1), max_delay)
    else:  # CONSTANT
        delay = initial_delay

    if jitter and jitter_factor > 0:
        import random
        jitter_range = delay * jitter_factor
        delay += random.uniform(-jitter_range, jitter_range)
        delay = max(0, delay)  # Ensure non-negative

    return delay


def should_retry(error: Exception, policy: RetryPolicy) -> bool:
    """Determine if an error should trigger a retry.

    Args:
        error: The exception that occurred
        policy: The retry policy

    Returns:
        True if the error matches retry criteria
    """
    # Check if it's a known retryable HangarError
    if is_retryable(error):
        return True

    # Check against policy's retry_on list
    error_type = type(error).__name__
    error_str = str(error).lower()

    for pattern in policy.retry_on:
        pattern_lower = pattern.lower()
        if pattern_lower in error_type.lower():
            return True
        if pattern_lower in error_str:
            return True

    return False


async def retry_async(
    operation: Callable[[], Any],
    policy: RetryPolicy,
    provider: str = "",
    operation_name: str = "",
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> RetryResult:
    """Execute an async operation with retry logic.

    Args:
        operation: Async callable to execute
        policy: Retry policy to use
        provider: Provider name for logging
        operation_name: Operation name for logging
        on_retry: Optional callback(attempt, error, delay) called before each retry

    Returns:
        RetryResult with success status, result, and attempt history
    """
    start_time = time.time()
    attempts: List[RetryAttempt] = []
    last_error: Optional[Exception] = None

    for attempt in range(policy.max_attempts):
        try:
            # Execute the operation
            if asyncio.iscoroutinefunction(operation):
                result = await operation()
            else:
                result = operation()

            # Success!
            total_time = time.time() - start_time

            if attempts:  # Had retries
                logger.info(
                    "retry_succeeded",
                    provider=provider,
                    operation=operation_name,
                    attempt=attempt + 1,
                    total_attempts=len(attempts) + 1,
                    total_time_s=round(total_time, 3),
                )

            return RetryResult(
                success=True,
                result=result,
                attempts=attempts,
                total_time_s=total_time,
            )

        except Exception as e:
            last_error = e
            error_type = type(e).__name__

            # Check if we should retry
            if attempt < policy.max_attempts - 1 and should_retry(e, policy):
                delay = calculate_backoff(
                    attempt=attempt,
                    strategy=policy.backoff,
                    initial_delay=policy.initial_delay,
                    max_delay=policy.max_delay,
                    jitter=policy.jitter,
                    jitter_factor=policy.jitter_factor,
                )

                # Record attempt
                attempts.append(RetryAttempt(
                    attempt_number=attempt + 1,
                    error_type=error_type,
                    error_message=str(e),
                    delay_before=delay,
                ))

                # Log retry
                logger.info(
                    "retry_attempt_failed",
                    provider=provider,
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_attempts=policy.max_attempts,
                    error_type=error_type,
                    error_preview=str(e)[:100],
                    retry_in_s=round(delay, 2),
                )

                # Callback if provided
                if on_retry:
                    try:
                        on_retry(attempt + 1, e, delay)
                    except Exception:
                        pass  # Ignore callback errors

                # Wait before retry
                await asyncio.sleep(delay)

            else:
                # No more retries or non-retryable error
                if attempts:
                    logger.warning(
                        "retry_exhausted",
                        provider=provider,
                        operation=operation_name,
                        total_attempts=len(attempts) + 1,
                        final_error_type=error_type,
                        final_error=str(e)[:200],
                    )
                break

    # All retries exhausted
    return RetryResult(
        success=False,
        final_error=last_error,
        attempts=attempts,
        total_time_s=time.time() - start_time,
    )


def retry_sync(
    operation: Callable[[], T],
    policy: RetryPolicy,
    provider: str = "",
    operation_name: str = "",
    on_retry: Optional[Callable[[int, Exception, float], None]] = None,
) -> RetryResult:
    """Execute a sync operation with retry logic.

    Args:
        operation: Callable to execute
        policy: Retry policy to use
        provider: Provider name for logging
        operation_name: Operation name for logging
        on_retry: Optional callback(attempt, error, delay) called before each retry

    Returns:
        RetryResult with success status, result, and attempt history
    """
    start_time = time.time()
    attempts: List[RetryAttempt] = []
    last_error: Optional[Exception] = None

    for attempt in range(policy.max_attempts):
        try:
            result = operation()

            # Success!
            total_time = time.time() - start_time

            if attempts:
                logger.info(
                    "retry_succeeded",
                    provider=provider,
                    operation=operation_name,
                    attempt=attempt + 1,
                    total_attempts=len(attempts) + 1,
                    total_time_s=round(total_time, 3),
                )

            return RetryResult(
                success=True,
                result=result,
                attempts=attempts,
                total_time_s=total_time,
            )

        except Exception as e:
            last_error = e
            error_type = type(e).__name__

            if attempt < policy.max_attempts - 1 and should_retry(e, policy):
                delay = calculate_backoff(
                    attempt=attempt,
                    strategy=policy.backoff,
                    initial_delay=policy.initial_delay,
                    max_delay=policy.max_delay,
                    jitter=policy.jitter,
                    jitter_factor=policy.jitter_factor,
                )

                attempts.append(RetryAttempt(
                    attempt_number=attempt + 1,
                    error_type=error_type,
                    error_message=str(e),
                    delay_before=delay,
                ))

                logger.info(
                    "retry_attempt_failed",
                    provider=provider,
                    operation=operation_name,
                    attempt=attempt + 1,
                    max_attempts=policy.max_attempts,
                    error_type=error_type,
                    error_preview=str(e)[:100],
                    retry_in_s=round(delay, 2),
                )

                if on_retry:
                    try:
                        on_retry(attempt + 1, e, delay)
                    except Exception:
                        pass

                time.sleep(delay)

            else:
                if attempts:
                    logger.warning(
                        "retry_exhausted",
                        provider=provider,
                        operation=operation_name,
                        total_attempts=len(attempts) + 1,
                        final_error_type=error_type,
                        final_error=str(e)[:200],
                    )
                break

    return RetryResult(
        success=False,
        final_error=last_error,
        attempts=attempts,
        total_time_s=time.time() - start_time,
    )


# =============================================================================
# Retry Configuration Store
# =============================================================================

class RetryConfigStore:
    """Stores retry configurations per provider.

    Allows loading retry policies from config.yaml and
    retrieving them for specific providers.
    """

    _default_policy: RetryPolicy
    _provider_policies: Dict[str, RetryPolicy]

    def __init__(self):
        self._default_policy = RetryPolicy()
        self._provider_policies = {}

    def set_default(self, policy: RetryPolicy) -> None:
        """Set the default retry policy."""
        self._default_policy = policy

    def set_provider_policy(self, provider_id: str, policy: RetryPolicy) -> None:
        """Set retry policy for a specific provider."""
        self._provider_policies[provider_id] = policy

    def get_policy(self, provider_id: str) -> RetryPolicy:
        """Get retry policy for a provider.

        Returns provider-specific policy if configured,
        otherwise returns default policy.
        """
        return self._provider_policies.get(provider_id, self._default_policy)

    def load_from_config(self, config: Dict[str, Any]) -> None:
        """Load retry configuration from config dictionary.

        Expected format:
            retry:
              default_policy:
                max_attempts: 3
                backoff: exponential
                ...
              per_provider:
                sqlite:
                  max_attempts: 5
                fetch:
                  max_attempts: 2
        """
        retry_config = config.get("retry", {})

        # Load default policy
        default_config = retry_config.get("default_policy", {})
        if default_config:
            self._default_policy = RetryPolicy.from_dict(default_config)
            logger.info(
                "retry_default_policy_loaded",
                max_attempts=self._default_policy.max_attempts,
                backoff=self._default_policy.backoff.value,
            )

        # Load per-provider policies
        per_provider = retry_config.get("per_provider", {})
        for provider_id, provider_config in per_provider.items():
            # Merge with default
            merged = self._default_policy.to_dict()
            merged.update(provider_config)
            self._provider_policies[provider_id] = RetryPolicy.from_dict(merged)
            logger.info(
                "retry_provider_policy_loaded",
                provider=provider_id,
                max_attempts=self._provider_policies[provider_id].max_attempts,
            )


# Global store instance
_retry_store = RetryConfigStore()


def get_retry_store() -> RetryConfigStore:
    """Get the global retry configuration store."""
    return _retry_store


def get_retry_policy(provider_id: str) -> RetryPolicy:
    """Get retry policy for a provider."""
    return _retry_store.get_policy(provider_id)


# =============================================================================
# Decorator
# =============================================================================

def with_retry(
    policy: Optional[RetryPolicy] = None,
    provider: str = "",
    operation: str = "",
):
    """Decorator to add retry logic to a function.

    Args:
        policy: Retry policy (uses default if None)
        provider: Provider name for logging
        operation: Operation name for logging

    Usage:
        @with_retry(RetryPolicy(max_attempts=5))
        async def risky_operation():
            ...
    """
    def decorator(func: Callable) -> Callable:
        import functools

        @functools.wraps(func)
        async def async_wrapper(*args, **kwargs):
            p = policy or _retry_store._default_policy
            result = await retry_async(
                lambda: func(*args, **kwargs),
                policy=p,
                provider=provider,
                operation_name=operation or func.__name__,
            )
            if result.success:
                return result.result
            raise result.final_error or Exception("Retry failed")

        @functools.wraps(func)
        def sync_wrapper(*args, **kwargs):
            p = policy or _retry_store._default_policy
            result = retry_sync(
                lambda: func(*args, **kwargs),
                policy=p,
                provider=provider,
                operation_name=operation or func.__name__,
            )
            if result.success:
                return result.result
            raise result.final_error or Exception("Retry failed")

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator

