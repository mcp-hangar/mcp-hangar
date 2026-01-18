"""Unit tests for CircuitBreaker."""

import time

from mcp_hangar.domain.model.circuit_breaker import CircuitBreaker, CircuitBreakerConfig, CircuitState


class TestCircuitBreakerConfig:
    """Tests for CircuitBreakerConfig."""

    def test_default_values(self):
        """Should have sensible defaults."""
        config = CircuitBreakerConfig()

        assert config.failure_threshold == 10
        assert config.reset_timeout_s == 60.0

    def test_enforces_minimum_threshold(self):
        """Should enforce minimum failure threshold of 1."""
        config = CircuitBreakerConfig(failure_threshold=0)

        assert config.failure_threshold == 1

    def test_enforces_minimum_timeout(self):
        """Should enforce minimum reset timeout of 1.0."""
        config = CircuitBreakerConfig(reset_timeout_s=0.5)

        assert config.reset_timeout_s == 1.0


class TestCircuitBreakerInitialState:
    """Tests for CircuitBreaker initial state."""

    def test_starts_closed(self):
        """Should start in CLOSED state."""
        cb = CircuitBreaker()

        assert cb.state == CircuitState.CLOSED
        assert cb.is_open is False

    def test_starts_with_zero_failures(self):
        """Should start with zero failure count."""
        cb = CircuitBreaker()

        assert cb.failure_count == 0

    def test_allows_requests_initially(self):
        """Should allow requests when circuit is closed."""
        cb = CircuitBreaker()

        assert cb.allow_request() is True


class TestCircuitBreakerRecordFailure:
    """Tests for recording failures."""

    def test_increments_failure_count(self):
        """record_failure should increment failure counter."""
        cb = CircuitBreaker()

        cb.record_failure()
        cb.record_failure()

        assert cb.failure_count == 2

    def test_opens_at_threshold(self):
        """Should open circuit when threshold reached."""
        config = CircuitBreakerConfig(failure_threshold=3)
        cb = CircuitBreaker(config)

        cb.record_failure()
        cb.record_failure()
        assert cb.is_open is False

        opened = cb.record_failure()

        assert opened is True
        assert cb.is_open is True
        assert cb.state == CircuitState.OPEN

    def test_returns_false_when_already_open(self):
        """record_failure returns False when circuit already open."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config)

        first = cb.record_failure()  # Opens circuit
        second = cb.record_failure()  # Already open

        assert first is True
        assert second is False

    def test_blocks_requests_when_open(self):
        """Should block requests when circuit is open."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config)

        cb.record_failure()

        assert cb.allow_request() is False


class TestCircuitBreakerRecordSuccess:
    """Tests for recording successes."""

    def test_resets_failure_count(self):
        """record_success should reset failure counter."""
        cb = CircuitBreaker()

        cb.record_failure()
        cb.record_failure()
        cb.record_success()

        assert cb.failure_count == 0

    def test_closes_open_circuit(self):
        """record_success should close an open circuit."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config)
        cb.record_failure()  # Open circuit

        cb.record_success()

        assert cb.is_open is False
        assert cb.state == CircuitState.CLOSED


class TestCircuitBreakerReset:
    """Tests for manual reset."""

    def test_closes_circuit(self):
        """reset should close the circuit."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config)
        cb.record_failure()

        cb.reset()

        assert cb.is_open is False

    def test_clears_failure_count(self):
        """reset should clear failure count."""
        cb = CircuitBreaker()
        cb.record_failure()
        cb.record_failure()

        cb.reset()

        assert cb.failure_count == 0


class TestCircuitBreakerAutoReset:
    """Tests for automatic reset after timeout."""

    def test_allows_request_after_timeout(self):
        """Should allow request after reset timeout elapsed."""
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout_s=1.0)
        cb = CircuitBreaker(config)
        cb.record_failure()

        # Manually set opened_at to past to simulate timeout
        cb._opened_at = time.time() - 2.0

        result = cb.allow_request()

        assert result is True
        assert cb.is_open is False

    def test_blocks_request_before_timeout(self):
        """Should block request before reset timeout."""
        config = CircuitBreakerConfig(failure_threshold=1, reset_timeout_s=60.0)
        cb = CircuitBreaker(config)
        cb.record_failure()

        result = cb.allow_request()

        assert result is False


class TestCircuitBreakerToDict:
    """Tests for serialization."""

    def test_includes_all_fields(self):
        """to_dict should include all relevant fields."""
        config = CircuitBreakerConfig(failure_threshold=5, reset_timeout_s=30.0)
        cb = CircuitBreaker(config)
        cb.record_failure()
        cb.record_failure()

        result = cb.to_dict()

        assert result["state"] == "closed"
        assert result["is_open"] is False
        assert result["failure_count"] == 2
        assert result["failure_threshold"] == 5
        assert result["reset_timeout_s"] == 30.0

    def test_shows_open_state(self):
        """to_dict should show open state correctly."""
        config = CircuitBreakerConfig(failure_threshold=1)
        cb = CircuitBreaker(config)
        cb.record_failure()

        result = cb.to_dict()

        assert result["state"] == "open"
        assert result["is_open"] is True
