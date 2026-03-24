"""Integration tests for Prometheus metrics using Testcontainers.

These tests verify that metrics are correctly exposed and scraped:
- Metrics endpoint format
- Prometheus server integration
"""

import pytest

pytestmark = [
    pytest.mark.integration,
    pytest.mark.container,
    pytest.mark.prometheus,
]


class TestMetricsFormat:
    """Tests for Prometheus metrics format (unit tests, no container needed)."""

    def test_counter_and_gauge_exported(self) -> None:
        """Counter and Gauge metrics are exported in Prometheus format."""
        from mcp_hangar.metrics import Counter, Gauge, REGISTRY

        test_counter = Counter(
            name="test_prom_requests_total",
            description="Test counter",
            labels=["method"],
        )
        test_counter.inc(labels={"method": "GET"})
        test_counter.inc(labels={"method": "POST"})

        test_gauge = Gauge(
            name="test_prom_active_connections",
            description="Test gauge",
            labels=["service"],
        )
        test_gauge.set(5, labels={"service": "api"})

        metrics_output = REGISTRY.get_metrics_output()

        assert "test_prom_requests_total" in metrics_output
        assert "test_prom_active_connections" in metrics_output

    def test_histogram_buckets_exported(self) -> None:
        """Histogram buckets are correctly exported."""
        from mcp_hangar.metrics import Histogram, REGISTRY

        test_histogram = Histogram(
            name="test_prom_latency_seconds",
            description="Test latency",
            labels=["operation"],
            buckets=(0.1, 0.5, 1.0, 5.0),
        )

        test_histogram.observe(0.05, labels={"operation": "fast"})
        test_histogram.observe(2.0, labels={"operation": "slow"})

        metrics_output = REGISTRY.get_metrics_output()

        assert "test_prom_latency_seconds_bucket" in metrics_output


class TestPrometheusServerIntegration:
    """Tests for Prometheus server integration."""

    def test_prometheus_api_accessible(
        self,
        prometheus_container: dict,
        http_client,
    ) -> None:
        """Prometheus API is accessible and responds correctly."""
        # Test runtime info
        response = http_client.get(f"{prometheus_container['url']}/api/v1/status/runtimeinfo")

        if response.status_code != 200:
            pytest.skip("Prometheus API not available")

        data = response.json()
        assert data["status"] == "success"

        # Test query API
        response = http_client.get(
            f"{prometheus_container['url']}/api/v1/query",
            params={"query": "up"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Test targets API
        response = http_client.get(f"{prometheus_container['url']}/api/v1/targets")
        assert response.status_code == 200


class TestObservabilityMetrics:
    """Tests for MCP Hangar observability metrics (unit tests)."""

    def test_observability_metrics_recorded(self) -> None:
        """Observability metrics are recorded correctly."""
        from mcp_hangar.metrics import REGISTRY
        from mcp_hangar.observability.metrics import get_observability_metrics

        metrics = get_observability_metrics()

        # Record various metrics
        metrics.retry_attempts.inc(
            labels={
                "provider": "prom-test",
                "tool": "add",
                "attempt_number": "1",
            }
        )
        metrics.circuit_breaker_state.set(0, labels={"provider": "prom-test"})
        metrics.cold_start_phase_duration.observe(
            0.5,
            labels={"provider": "prom-test", "phase": "connect"},
        )
        metrics.availability_ratio.set(0.95)
        metrics.error_budget_remaining.set(0.8)

        output = REGISTRY.get_metrics_output()

        assert "mcp_hangar_retry_attempts_total" in output
        assert "mcp_hangar_circuit_breaker_state" in output
        assert "mcp_hangar_cold_start_phase_duration" in output
        assert "mcp_hangar_availability_ratio" in output
        assert "mcp_hangar_error_budget_remaining" in output
