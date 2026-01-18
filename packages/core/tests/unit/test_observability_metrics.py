"""Tests for observability/metrics module."""

import pytest

from mcp_hangar.observability.metrics import CircuitState, get_observability_metrics, reset_observability_metrics


class TestCircuitState:
    """Tests for CircuitState enum."""

    def test_has_closed_state(self):
        """Should have CLOSED state."""
        assert CircuitState.CLOSED.value == "closed"

    def test_has_open_state(self):
        """Should have OPEN state."""
        assert CircuitState.OPEN.value == "open"

    def test_has_half_open_state(self):
        """Should have HALF_OPEN state."""
        assert CircuitState.HALF_OPEN.value == "half_open"


class TestObservabilityMetrics:
    """Tests for ObservabilityMetrics class."""

    @pytest.fixture(autouse=True)
    def reset_metrics(self):
        """Reset metrics before and after each test."""
        reset_observability_metrics()
        yield
        reset_observability_metrics()

    def test_singleton_pattern(self):
        """Should return same instance."""
        m1 = get_observability_metrics()
        m2 = get_observability_metrics()
        assert m1 is m2

    def test_set_circuit_state(self):
        """Should set circuit breaker state."""
        metrics = get_observability_metrics()
        metrics.set_circuit_state("test-provider", CircuitState.OPEN)
        # Should not raise

    def test_record_circuit_failure(self):
        """Should record circuit failure."""
        metrics = get_observability_metrics()
        metrics.record_circuit_failure("test-provider")
        # Should not raise

    def test_record_circuit_success(self):
        """Should record circuit success."""
        metrics = get_observability_metrics()
        metrics.record_circuit_success("test-provider")
        # Should not raise

    def test_record_retry_attempt(self):
        """Should record retry attempt."""
        metrics = get_observability_metrics()
        metrics.record_retry_attempt("provider", "tool", 1)
        metrics.record_retry_attempt("provider", "tool", 2)
        # Should not raise

    def test_record_retry_exhausted(self):
        """Should record retry exhaustion."""
        metrics = get_observability_metrics()
        metrics.record_retry_exhausted("provider", "tool")
        # Should not raise

    def test_record_retry_success(self):
        """Should record retry success."""
        metrics = get_observability_metrics()
        metrics.record_retry_success("provider", "tool", 3)
        # Should not raise

    def test_set_pending_requests(self):
        """Should set pending request count."""
        metrics = get_observability_metrics()
        metrics.set_pending_requests("provider", 5)
        metrics.set_pending_requests("provider", 0)
        # Should not raise

    def test_observe_queue_time(self):
        """Should observe queue time."""
        metrics = get_observability_metrics()
        metrics.observe_queue_time("provider", 0.5)
        # Should not raise

    def test_record_cold_start_phase(self):
        """Should record cold start phase duration."""
        metrics = get_observability_metrics()
        metrics.record_cold_start_phase("provider", "spawn", 0.1)
        metrics.record_cold_start_phase("provider", "connect", 0.2)
        metrics.record_cold_start_phase("provider", "discover", 0.3)
        # Should not raise

    def test_cold_start_began_and_completed(self):
        """Should track cold start progress."""
        metrics = get_observability_metrics()
        metrics.cold_start_began("provider")
        metrics.cold_start_completed("provider")
        # Should not raise

    def test_update_provider_resources(self):
        """Should update resource metrics."""
        metrics = get_observability_metrics()
        metrics.update_provider_resources(
            "provider",
            memory_bytes=1024 * 1024 * 100,
            cpu_percent=25.5,
        )
        # Should not raise

    def test_update_availability(self):
        """Should update availability ratio."""
        metrics = get_observability_metrics()
        metrics.update_availability(8, 10)  # 80%
        metrics.update_availability(0, 0)  # Edge case: no providers
        # Should not raise

    def test_update_error_budget(self):
        """Should update error budget."""
        metrics = get_observability_metrics()
        metrics.update_error_budget(0.95)
        metrics.update_error_budget(1.5)  # Should clamp to 1.0
        metrics.update_error_budget(-0.5)  # Should clamp to 0.0
        # Should not raise

    def test_update_utilization(self):
        """Should update utilization."""
        metrics = get_observability_metrics()
        metrics.update_utilization("provider", 0.75)
        # Should not raise


class TestResetObservabilityMetrics:
    """Tests for reset_observability_metrics function."""

    def test_resets_singleton(self):
        """Should reset the singleton instance."""
        m1 = get_observability_metrics()
        reset_observability_metrics()
        m2 = get_observability_metrics()
        # Should be different instances after reset
        assert m1 is not m2
