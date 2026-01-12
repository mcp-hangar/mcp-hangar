"""Tests for retry module."""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.errors import ConfigurationError, TransientError
from mcp_hangar.retry import (
    BackoffStrategy,
    calculate_backoff,
    get_retry_store,
    retry_async,
    retry_sync,
    RetryAttempt,
    RetryPolicy,
    RetryResult,
    should_retry,
    with_retry,
)


class TestRetryPolicy:
    """Tests for RetryPolicy configuration."""

    def test_default_policy(self):
        """Test default retry policy values."""
        policy = RetryPolicy()
        assert policy.max_attempts == 3
        assert policy.backoff == BackoffStrategy.EXPONENTIAL
        assert policy.initial_delay == 1.0
        assert policy.max_delay == 30.0
        assert policy.jitter is True

    def test_from_dict(self):
        """Test creating policy from dictionary."""
        config = {
            "max_attempts": 5,
            "backoff": "linear",
            "initial_delay": 0.5,
            "max_delay": 10.0,
        }
        policy = RetryPolicy.from_dict(config)
        assert policy.max_attempts == 5
        assert policy.backoff == BackoffStrategy.LINEAR
        assert policy.initial_delay == 0.5

    def test_to_dict(self):
        """Test serializing policy to dictionary."""
        policy = RetryPolicy(max_attempts=4, backoff=BackoffStrategy.CONSTANT)
        result = policy.to_dict()
        assert result["max_attempts"] == 4
        assert result["backoff"] == "constant"


class TestCalculateBackoff:
    """Tests for backoff calculation."""

    def test_exponential_backoff(self):
        """Test exponential backoff calculation."""
        # 2^0 * 1.0 = 1.0
        delay = calculate_backoff(0, BackoffStrategy.EXPONENTIAL, 1.0, 30.0, jitter=False)
        assert delay == 1.0

        # 2^1 * 1.0 = 2.0
        delay = calculate_backoff(1, BackoffStrategy.EXPONENTIAL, 1.0, 30.0, jitter=False)
        assert delay == 2.0

        # 2^2 * 1.0 = 4.0
        delay = calculate_backoff(2, BackoffStrategy.EXPONENTIAL, 1.0, 30.0, jitter=False)
        assert delay == 4.0

    def test_exponential_backoff_with_max_cap(self):
        """Test that exponential backoff respects max_delay."""
        delay = calculate_backoff(10, BackoffStrategy.EXPONENTIAL, 1.0, 30.0, jitter=False)
        assert delay == 30.0  # Capped at max_delay

    def test_linear_backoff(self):
        """Test linear backoff calculation."""
        delay = calculate_backoff(0, BackoffStrategy.LINEAR, 1.0, 30.0, jitter=False)
        assert delay == 1.0

        delay = calculate_backoff(1, BackoffStrategy.LINEAR, 1.0, 30.0, jitter=False)
        assert delay == 2.0

        delay = calculate_backoff(2, BackoffStrategy.LINEAR, 1.0, 30.0, jitter=False)
        assert delay == 3.0

    def test_constant_backoff(self):
        """Test constant backoff calculation."""
        delay = calculate_backoff(0, BackoffStrategy.CONSTANT, 2.0, 30.0, jitter=False)
        assert delay == 2.0

        delay = calculate_backoff(5, BackoffStrategy.CONSTANT, 2.0, 30.0, jitter=False)
        assert delay == 2.0

    def test_jitter_applied(self):
        """Test that jitter is applied to delays."""
        delays = set()
        for _ in range(10):
            delay = calculate_backoff(1, BackoffStrategy.EXPONENTIAL, 1.0, 30.0, jitter=True, jitter_factor=0.5)
            delays.add(round(delay, 2))

        # With jitter, we should get varying delays
        assert len(delays) > 1


class TestShouldRetry:
    """Tests for should_retry logic."""

    def test_transient_error_should_retry(self):
        """Test that TransientError triggers retry."""
        error = TransientError(message="temp failure")
        policy = RetryPolicy()
        assert should_retry(error, policy) is True

    def test_config_error_should_not_retry(self):
        """Test that ConfigurationError does not trigger retry."""
        error = ConfigurationError(message="bad config")
        policy = RetryPolicy(retry_on=["Timeout"])
        assert should_retry(error, policy) is False

    def test_pattern_matching_retry(self):
        """Test pattern-based retry matching."""
        error = Exception("Connection timeout occurred")
        policy = RetryPolicy(retry_on=["Timeout"])
        assert should_retry(error, policy) is True


class TestRetrySyncExecution:
    """Tests for synchronous retry execution."""

    def test_success_on_first_attempt(self):
        """Test successful operation on first attempt."""
        operation = MagicMock(return_value={"result": "success"})
        policy = RetryPolicy(max_attempts=3)

        result = retry_sync(operation, policy, provider="test")

        assert result.success is True
        assert result.result == {"result": "success"}
        assert result.attempt_count == 1
        assert len(result.attempts) == 0
        operation.assert_called_once()

    def test_success_after_retry(self):
        """Test success after failed attempts."""
        call_count = [0]

        def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise TransientError(message="Temporary failure")
            return {"result": "success"}

        policy = RetryPolicy(max_attempts=3, initial_delay=0.01)
        result = retry_sync(failing_then_success, policy, provider="test")

        assert result.success is True
        assert len(result.attempts) == 2  # 2 failures before success

    def test_all_retries_exhausted(self):
        """Test when all retries are exhausted."""
        operation = MagicMock(side_effect=TransientError(message="Always fails"))
        policy = RetryPolicy(max_attempts=3, initial_delay=0.01)

        result = retry_sync(operation, policy, provider="test")

        assert result.success is False
        assert result.final_error is not None
        assert len(result.attempts) == 2  # max_attempts - 1

    def test_non_retryable_error_fails_immediately(self):
        """Test that non-retryable errors fail immediately."""
        operation = MagicMock(side_effect=ConfigurationError(message="Bad config"))
        policy = RetryPolicy(max_attempts=3, retry_on=["Timeout"])

        result = retry_sync(operation, policy, provider="test")

        assert result.success is False
        operation.assert_called_once()  # No retries

    def test_on_retry_callback(self):
        """Test that on_retry callback is invoked."""
        call_count = [0]
        callback_calls = []

        def failing_op():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TransientError(message="Fail")
            return "ok"

        def on_retry(attempt, error, delay):
            callback_calls.append((attempt, str(error), delay))

        policy = RetryPolicy(max_attempts=3, initial_delay=0.01)
        retry_sync(failing_op, policy, on_retry=on_retry)

        assert len(callback_calls) == 1
        assert callback_calls[0][0] == 1


class TestRetryResult:
    """Tests for RetryResult."""

    def test_to_dict(self):
        """Test result serialization."""
        result = RetryResult(
            success=True,
            result={"data": "value"},
            attempts=[
                RetryAttempt(
                    attempt_number=1,
                    error_type="TransientError",
                    error_message="Temp fail",
                    delay_before=1.0,
                ),
            ],
            total_time_s=2.5,
        )
        data = result.to_dict()
        assert data["success"] is True
        assert data["attempt_count"] == 2
        assert data["total_time_s"] == 2.5


class TestRetryConfigStore:
    """Tests for retry configuration store."""

    def test_default_policy(self):
        """Test getting default policy."""
        store = get_retry_store()
        policy = store.get_policy("unknown-provider")
        assert isinstance(policy, RetryPolicy)

    def test_load_from_config(self):
        """Test loading configuration from dict."""
        store = get_retry_store()
        config = {
            "retry": {
                "default_policy": {
                    "max_attempts": 5,
                },
                "per_provider": {
                    "sqlite": {
                        "max_attempts": 10,
                    },
                },
            },
        }
        store.load_from_config(config)

        # Check default was updated
        default = store.get_policy("unknown")
        assert default.max_attempts == 5

        # Check provider-specific
        sqlite = store.get_policy("sqlite")
        assert sqlite.max_attempts == 10


@pytest.mark.asyncio
class TestRetryAsyncExecution:
    """Tests for async retry execution."""

    async def test_async_success(self):
        """Test successful async operation."""

        async def operation():
            return {"result": "async_success"}

        policy = RetryPolicy(max_attempts=3)
        result = await retry_async(operation, policy)

        assert result.success is True
        assert result.result == {"result": "async_success"}

    async def test_async_retry_on_failure(self):
        """Test async retry on failure."""
        call_count = [0]

        async def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TransientError(message="Async failure")
            return "ok"

        policy = RetryPolicy(max_attempts=3, initial_delay=0.01)
        result = await retry_async(failing_then_success, policy)

        assert result.success is True
        assert call_count[0] == 2


class TestWithRetryDecorator:
    """Tests for @with_retry decorator."""

    def test_decorator_on_sync_function(self):
        """Test decorator on synchronous function."""
        call_count = [0]

        @with_retry(RetryPolicy(max_attempts=3, initial_delay=0.01))
        def sometimes_fails():
            call_count[0] += 1
            if call_count[0] < 2:
                raise TransientError(message="Fail")
            return "success"

        result = sometimes_fails()
        assert result == "success"
        assert call_count[0] == 2

    def test_decorator_raises_on_exhausted_retries(self):
        """Test that decorator raises when retries exhausted."""

        @with_retry(RetryPolicy(max_attempts=2, initial_delay=0.01))
        def always_fails():
            raise TransientError(message="Always fails")

        with pytest.raises(TransientError):
            always_fails()


class TestRetryConfigStoreAdvanced:
    """Advanced tests for RetryConfigStore."""

    def test_set_provider_policy(self):
        """Test setting provider-specific policy."""
        store = get_retry_store()
        policy = RetryPolicy(max_attempts=10)
        store.set_provider_policy("custom-provider", policy)

        retrieved = store.get_policy("custom-provider")
        assert retrieved.max_attempts == 10


class TestRetrySyncAdvanced:
    """Advanced tests for retry_sync."""

    def test_retry_with_all_callbacks(self):
        """Test retry with logging and callbacks."""
        call_count = [0]
        retry_calls = []

        def on_retry_callback(attempt, error, delay):
            retry_calls.append((attempt, str(error), delay))

        def failing_then_success():
            call_count[0] += 1
            if call_count[0] < 3:
                raise TransientError(message=f"Fail #{call_count[0]}")
            return {"result": "ok"}

        policy = RetryPolicy(max_attempts=5, initial_delay=0.01)
        result = retry_sync(
            failing_then_success,
            policy=policy,
            provider="test-provider",
            operation_name="test-op",
            on_retry=on_retry_callback,
        )

        assert result.success is True
        assert len(retry_calls) == 2  # 2 failures before success
        assert retry_calls[0][0] == 1
        assert retry_calls[1][0] == 2

    def test_retry_first_attempt_success_no_attempts_list(self):
        """Test that successful first attempt has empty attempts list."""
        policy = RetryPolicy(max_attempts=3)
        result = retry_sync(
            lambda: "immediate_success",
            policy=policy,
        )
        assert result.success is True
        assert result.attempts == []
        assert result.attempt_count == 1
