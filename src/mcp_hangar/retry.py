"""Automatic retry with exponential backoff.

This module provides retry functionality for transient failures,
including:

- Configurable retry policies
- Exponential, linear, and constant backoff strategies
- Per-mcp_server retry configuration
- Circuit breaker integration

Usage example::

    from mcp_hangar import RetryPolicy, BackoffStrategy, with_retry

    policy = RetryPolicy(
        max_attempts=3,
        backoff=BackoffStrategy.EXPONENTIAL
    )

    @with_retry(policy)
    def call_mcp_server():
        return risky_operation()

"""

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
import inspect
import time
from typing import Any, TypeVar

from .domain.exceptions import (
    AuthenticationError,
    AuthorizationError,
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    RateLimitExceeded,
    ToolAccessDeniedError,
    ValidationError,
)
from .errors import is_retryable
from .logging_config import get_logger

logger = get_logger(__name__)

T = TypeVar("T")

#: Errors that are a decision, not a failure. Retrying one asks the same
#: question again and gets the same answer -- or worse: re-driving an approval
#: gate holds a human decision open once per attempt, and re-driving a rate
#: limit is what the limit exists to stop. Checked before the policy, so no
#: `retry_on` can opt back in.
#:
#: `ValidationError` is in the list for a second reason: its message frequently
#: contains "malformed", which `is_retryable` matches as a transient shape, so
#: an invalid payload was retried to exhaustion on the stock configuration.
_NEVER_RETRY: tuple[type[Exception], ...] = (
    ToolAccessDeniedError,
    EgressPolicyDeniedError,
    EgressPolicyApprovalRequiredError,
    AuthorizationError,
    AuthenticationError,
    RateLimitExceeded,
    ValidationError,
)


class BackoffStrategy(StrEnum):
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
    retry_on: list[str] = field(
        default_factory=lambda: [
            "MalformedJSON",
            "JSONDecodeError",
            "Timeout",
            "TimeoutError",
            "ConnectionError",
            "McpServerNotResponding",
            "TransientError",
            "McpServerProtocolError",
            "NetworkError",
        ]
    )
    jitter: bool = True
    jitter_factor: float = 0.25

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RetryPolicy":
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
            "McpServerNotResponding",
            "TransientError",
            "McpServerProtocolError",
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

    def to_dict(self) -> dict[str, Any]:
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
    final_error: Exception | None = None
    attempts: list[RetryAttempt] = field(default_factory=list)
    total_time_s: float = 0.0

    @property
    def attempt_count(self) -> int:
        """Total number of attempts made."""
        return len(self.attempts) + (1 if self.success else 0)

    def to_dict(self) -> dict[str, Any]:
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
        delay = min(initial_delay * (2**attempt), max_delay)
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

    return float(delay)


def should_retry(error: Exception, policy: RetryPolicy) -> bool:
    """Determine if an error should trigger a retry.

    A refusal is never retried, whatever the policy says. Both arms below are
    substring matches -- on the type name AND on the message -- so a
    `retry_on` of `["Error"]` matches every refusal type there is, and even the
    stock list retries a denial whose message happens to contain "timeout" or
    "connection". Re-asking an approval gate, or re-driving a denied egress
    decision, is not a transient-failure recovery: the answer was the point.

    Args:
        error: The exception that occurred
        policy: The retry policy

    Returns:
        True if the error matches retry criteria
    """
    if isinstance(error, _NEVER_RETRY):
        return False

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
    mcp_server: str = "",
    operation_name: str = "",
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> RetryResult:
    """Execute an async operation with retry logic.

    Args:
        operation: Async callable to execute
        policy: Retry policy to use
        mcp_server: McpServer name for logging
        operation_name: Operation name for logging
        on_retry: Optional callback(attempt, error, delay) called before each retry

    Returns:
        RetryResult with success status, result, and attempt history
    """
    start_time = time.time()
    attempts: list[RetryAttempt] = []
    last_error: Exception | None = None

    for attempt in range(policy.max_attempts):
        try:
            # Execute the operation
            if inspect.iscoroutinefunction(operation):
                result = await operation()
            else:
                result = operation()

            # Success!
            total_time = time.time() - start_time

            if attempts:  # Had retries
                logger.info(
                    "retry_succeeded",
                    mcp_server=mcp_server,
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

        except Exception as e:  # noqa: BLE001 -- fault-barrier: retry framework must catch all errors to manage retry logic
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
                attempts.append(
                    RetryAttempt(
                        attempt_number=attempt + 1,
                        error_type=error_type,
                        error_message=str(e),
                        delay_before=delay,
                    )
                )

                # Log retry
                logger.info(
                    "retry_attempt_failed",
                    mcp_server=mcp_server,
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
                    except Exception:  # noqa: BLE001 -- fault-barrier: retry callback must not break retry loop
                        pass  # Ignore callback errors

                # Wait before retry
                await asyncio.sleep(delay)

            else:
                # No more retries or non-retryable error
                if attempts:
                    logger.warning(
                        "retry_exhausted",
                        mcp_server=mcp_server,
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
    mcp_server: str = "",
    operation_name: str = "",
    on_retry: Callable[[int, Exception, float], None] | None = None,
) -> RetryResult:
    """Execute a sync operation with retry logic.

    Args:
        operation: Callable to execute
        policy: Retry policy to use
        mcp_server: McpServer name for logging
        operation_name: Operation name for logging
        on_retry: Optional callback(attempt, error, delay) called before each retry

    Returns:
        RetryResult with success status, result, and attempt history
    """
    start_time = time.time()
    attempts: list[RetryAttempt] = []
    last_error: Exception | None = None

    for attempt in range(policy.max_attempts):
        try:
            result = operation()

            # Success!
            total_time = time.time() - start_time

            if attempts:
                logger.info(
                    "retry_succeeded",
                    mcp_server=mcp_server,
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

        except Exception as e:  # noqa: BLE001 -- fault-barrier: retry framework must catch all errors to manage retry logic
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

                attempts.append(
                    RetryAttempt(
                        attempt_number=attempt + 1,
                        error_type=error_type,
                        error_message=str(e),
                        delay_before=delay,
                    )
                )

                logger.info(
                    "retry_attempt_failed",
                    mcp_server=mcp_server,
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
                    except (TypeError, ValueError, RuntimeError) as callback_err:
                        logger.debug("retry_callback_error", error=str(callback_err))

                time.sleep(delay)

            else:
                if attempts:
                    logger.warning(
                        "retry_exhausted",
                        mcp_server=mcp_server,
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
    """Stores retry configurations per mcp_server.

    Allows loading retry policies from config.yaml and
    retrieving them for specific mcp_servers.
    """

    _default_policy: RetryPolicy
    _mcp_server_policies: dict[str, RetryPolicy]
    _default_is_configured: bool

    def __init__(self):
        self._default_policy = RetryPolicy()
        self._mcp_server_policies = {}
        # Whether anyone actually asked for a default. `RetryPolicy()` is three
        # attempts, so a store that cannot tell "the operator wrote
        # `default_policy`" from "nobody said anything" would turn every batch
        # call in every deployment into three -- a retry storm shipped as a
        # bug fix. See `configured_policy_for`.
        self._default_is_configured = False

    def set_default(self, policy: RetryPolicy) -> None:
        """Set the default retry policy."""
        self._default_policy = policy
        self._default_is_configured = True

    def set_mcp_server_policy(self, mcp_server_id: str, policy: RetryPolicy) -> None:
        """Set retry policy for a specific mcp_server."""
        self._mcp_server_policies[mcp_server_id] = policy

    def get_policy(self, mcp_server_id: str) -> RetryPolicy:
        """Get retry policy for a mcp_server.

        Returns mcp_server-specific policy if configured,
        otherwise returns default policy.
        """
        return self._mcp_server_policies.get(mcp_server_id, self._default_policy)

    def configured_policy_for(self, mcp_server_id: str) -> RetryPolicy | None:
        """The policy an operator wrote for this server, or ``None``.

        The difference from :meth:`get_policy` is the whole point: that one
        always answers, falling back to a `RetryPolicy()` nobody asked for.
        A caller deciding whether retries happen at all needs to know that
        nothing was configured, so it can leave behaviour as it was.
        """
        configured = self._mcp_server_policies.get(mcp_server_id)
        if configured is not None:
            return configured
        return self._default_policy if self._default_is_configured else None

    def load_from_config(self, config: dict[str, Any]) -> None:
        """Load retry configuration from config dictionary.

        Expected format:
            retry:
              default_policy:
                max_attempts: 3
                backoff: exponential
                ...
              per_mcp_server:
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
            self._default_is_configured = True
            logger.info(
                "retry_default_policy_loaded",
                max_attempts=self._default_policy.max_attempts,
                backoff=self._default_policy.backoff.value,
            )

        # Load per-mcp_server policies
        per_mcp_server = retry_config.get("per_mcp_server", {})
        for mcp_server_id, mcp_server_config in per_mcp_server.items():
            # Merge with default
            merged = self._default_policy.to_dict()
            merged.update(mcp_server_config)
            self._mcp_server_policies[mcp_server_id] = RetryPolicy.from_dict(merged)
            logger.info(
                "retry_mcp_server_policy_loaded",
                mcp_server=mcp_server_id,
                max_attempts=self._mcp_server_policies[mcp_server_id].max_attempts,
            )


# Global store instance
_retry_store = RetryConfigStore()


def get_retry_store() -> RetryConfigStore:
    """Get the global retry configuration store."""
    return _retry_store


def get_retry_policy(mcp_server_id: str) -> RetryPolicy:
    """Get retry policy for a mcp_server."""
    return _retry_store.get_policy(mcp_server_id)


def configured_retry_policy(mcp_server_id: str) -> RetryPolicy | None:
    """The configured retry policy for a server, or ``None`` if none was set."""
    return _retry_store.configured_policy_for(mcp_server_id)


def reset_retry_store() -> None:
    """Forget every loaded policy (config reload, and tests).

    In place rather than rebinding the global: `bootstrap.retry_config` holds
    the store it fetched, and so does anything else that took a reference, so a
    fresh object would leave them reading the old one -- the shape that makes a
    reload look applied and change nothing.
    """
    _retry_store._mcp_server_policies.clear()
    _retry_store._default_policy = RetryPolicy()
    _retry_store._default_is_configured = False


# =============================================================================
# Decorator
# =============================================================================


def with_retry(
    policy: RetryPolicy | None = None,
    mcp_server: str = "",
    operation: str = "",
):
    """Decorator to add retry logic to a function.

    Args:
        policy: Retry policy (uses default if None)
        mcp_server: McpServer name for logging
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
                mcp_server=mcp_server,
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
                mcp_server=mcp_server,
                operation_name=operation or func.__name__,
            )
            if result.success:
                return result.result
            raise result.final_error or Exception("Retry failed")

        if inspect.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator
