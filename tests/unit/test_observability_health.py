"""Tests for observability/health module."""

import asyncio

import pytest

from mcp_hangar.observability.health import (
    create_event_loop_health_check,
    create_memory_health_check,
    create_provider_health_check,
    get_health_endpoint,
    HealthCheck,
    HealthCheckResult,
    HealthResponse,
    HealthStatus,
    reset_health_endpoint,
)


class TestHealthStatus:
    """Tests for HealthStatus enum."""

    def test_has_healthy_status(self):
        """Should have HEALTHY status."""
        assert HealthStatus.HEALTHY.value == "healthy"

    def test_has_degraded_status(self):
        """Should have DEGRADED status."""
        assert HealthStatus.DEGRADED.value == "degraded"

    def test_has_unhealthy_status(self):
        """Should have UNHEALTHY status."""
        assert HealthStatus.UNHEALTHY.value == "unhealthy"

    def test_has_unknown_status(self):
        """Should have UNKNOWN status."""
        assert HealthStatus.UNKNOWN.value == "unknown"


class TestHealthCheckResult:
    """Tests for HealthCheckResult dataclass."""

    def test_to_dict(self):
        """Should convert to dictionary."""
        result = HealthCheckResult(
            name="test",
            status=HealthStatus.HEALTHY,
            message="OK",
            duration_ms=1.5,
        )
        d = result.to_dict()

        assert d["name"] == "test"
        assert d["status"] == "healthy"
        assert d["message"] == "OK"
        assert d["duration_ms"] == 1.5

    def test_default_values(self):
        """Should have sensible defaults."""
        result = HealthCheckResult(name="test", status=HealthStatus.HEALTHY)

        assert result.message == ""
        assert result.duration_ms == 0.0
        assert result.details == {}


class TestHealthResponse:
    """Tests for HealthResponse dataclass."""

    def test_to_dict(self):
        """Should convert to dictionary."""
        response = HealthResponse(
            status=HealthStatus.HEALTHY,
            checks=[HealthCheckResult(name="test", status=HealthStatus.HEALTHY)],
            version="1.0.0",
            uptime_seconds=100.0,
        )
        d = response.to_dict()

        assert d["status"] == "healthy"
        assert len(d["checks"]) == 1
        assert d["version"] == "1.0.0"
        assert d["uptime_seconds"] == 100.0


class TestHealthCheck:
    """Tests for HealthCheck class."""

    @pytest.mark.asyncio
    async def test_execute_success(self):
        """Should return healthy for successful check."""
        check = HealthCheck(
            name="test",
            check_fn=lambda: True,
            timeout_seconds=1.0,
        )
        result = await check.execute()

        assert result.status == HealthStatus.HEALTHY
        assert result.name == "test"

    @pytest.mark.asyncio
    async def test_execute_failure(self):
        """Should return unhealthy for failed check."""
        check = HealthCheck(
            name="test",
            check_fn=lambda: False,
            timeout_seconds=1.0,
            critical=True,
        )
        result = await check.execute()

        assert result.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_execute_non_critical_failure(self):
        """Should return degraded for non-critical failure."""
        check = HealthCheck(
            name="test",
            check_fn=lambda: False,
            timeout_seconds=1.0,
            critical=False,
        )
        result = await check.execute()

        assert result.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_execute_exception(self):
        """Should handle exceptions gracefully."""

        def failing_check():
            raise ValueError("Test error")

        check = HealthCheck(
            name="test",
            check_fn=failing_check,
            timeout_seconds=1.0,
        )
        result = await check.execute()

        assert result.status == HealthStatus.UNHEALTHY
        assert "Test error" in result.message

    @pytest.mark.asyncio
    async def test_execute_timeout(self):
        """Should handle timeout."""
        import time

        def slow_check():
            time.sleep(2)
            return True

        check = HealthCheck(
            name="test",
            check_fn=slow_check,
            timeout_seconds=0.1,
        )
        result = await check.execute()

        assert result.status == HealthStatus.UNHEALTHY
        assert "timed out" in result.message.lower()

    @pytest.mark.asyncio
    async def test_execute_async_check(self):
        """Should handle async check functions."""

        async def async_check():
            await asyncio.sleep(0.01)
            return True

        check = HealthCheck(
            name="test",
            check_fn=async_check,
            timeout_seconds=1.0,
        )
        result = await check.execute()

        assert result.status == HealthStatus.HEALTHY


class TestHealthEndpoint:
    """Tests for HealthEndpoint class."""

    @pytest.fixture(autouse=True)
    def reset_endpoint(self):
        """Reset endpoint before and after each test."""
        reset_health_endpoint()
        yield
        reset_health_endpoint()

    def test_singleton_pattern(self):
        """Should return same instance."""
        e1 = get_health_endpoint()
        e2 = get_health_endpoint()
        assert e1 is e2

    def test_register_check(self):
        """Should register health check."""
        endpoint = get_health_endpoint()
        check = HealthCheck(name="test", check_fn=lambda: True)

        endpoint.register_check(check)
        # Should not raise

    def test_register_check_no_duplicates(self):
        """Should not register duplicate checks."""
        endpoint = get_health_endpoint()
        check1 = HealthCheck(name="test", check_fn=lambda: True)
        check2 = HealthCheck(name="test", check_fn=lambda: False)

        endpoint.register_check(check1)
        endpoint.register_check(check2)

        # Should only have one check
        assert len(endpoint._checks) == 1

    def test_unregister_check(self):
        """Should unregister health check."""
        endpoint = get_health_endpoint()
        check = HealthCheck(name="test", check_fn=lambda: True)

        endpoint.register_check(check)
        endpoint.unregister_check("test")

        assert len(endpoint._checks) == 0

    def test_mark_startup_complete(self):
        """Should mark startup complete."""
        endpoint = get_health_endpoint()
        assert endpoint._startup_complete is False

        endpoint.mark_startup_complete()
        assert endpoint._startup_complete is True

    @pytest.mark.asyncio
    async def test_check_liveness(self):
        """Should always return healthy for liveness."""
        endpoint = get_health_endpoint()
        response = await endpoint.check_liveness()

        assert response.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_startup_before_complete(self):
        """Should return unhealthy before startup complete."""
        endpoint = get_health_endpoint()
        response = await endpoint.check_startup()

        assert response.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_check_startup_after_complete(self):
        """Should return healthy after startup complete."""
        endpoint = get_health_endpoint()
        endpoint.mark_startup_complete()
        response = await endpoint.check_startup()

        assert response.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_readiness_no_checks(self):
        """Should return healthy when no checks registered."""
        endpoint = get_health_endpoint()
        response = await endpoint.check_readiness()

        assert response.status == HealthStatus.HEALTHY

    @pytest.mark.asyncio
    async def test_check_readiness_all_pass(self):
        """Should return healthy when all checks pass."""
        endpoint = get_health_endpoint()
        endpoint.register_check(HealthCheck(name="check1", check_fn=lambda: True))
        endpoint.register_check(HealthCheck(name="check2", check_fn=lambda: True))

        response = await endpoint.check_readiness()

        assert response.status == HealthStatus.HEALTHY
        assert len(response.checks) == 2

    @pytest.mark.asyncio
    async def test_check_readiness_one_fails(self):
        """Should return unhealthy when one critical check fails."""
        endpoint = get_health_endpoint()
        endpoint.register_check(HealthCheck(name="pass", check_fn=lambda: True))
        endpoint.register_check(HealthCheck(name="fail", check_fn=lambda: False, critical=True))

        response = await endpoint.check_readiness()

        assert response.status == HealthStatus.UNHEALTHY

    @pytest.mark.asyncio
    async def test_check_readiness_degraded(self):
        """Should return degraded when non-critical check fails."""
        endpoint = get_health_endpoint()
        endpoint.register_check(HealthCheck(name="pass", check_fn=lambda: True))
        endpoint.register_check(HealthCheck(name="fail", check_fn=lambda: False, critical=False))

        response = await endpoint.check_readiness()

        assert response.status == HealthStatus.DEGRADED

    @pytest.mark.asyncio
    async def test_get_detailed_status(self):
        """Should return detailed status."""
        endpoint = get_health_endpoint()
        endpoint.register_check(HealthCheck(name="test", check_fn=lambda: True))
        endpoint.mark_startup_complete()

        status = await endpoint.get_detailed_status()

        assert "status" in status
        assert "summary" in status
        assert "checks" in status
        assert status["startup_complete"] is True


class TestBuiltInHealthChecks:
    """Tests for built-in health check factories."""

    def test_create_provider_health_check(self):
        """Should create provider health check."""
        providers = {}
        check = create_provider_health_check(providers)

        assert check.name == "providers"
        assert check.critical is False

    @pytest.mark.asyncio
    async def test_provider_health_check_empty(self):
        """Should be healthy with no providers."""
        providers = {}
        check = create_provider_health_check(providers)
        result = await check.execute()

        assert result.status == HealthStatus.HEALTHY

    def test_create_memory_health_check(self):
        """Should create memory health check."""
        check = create_memory_health_check(threshold_mb=2048)

        assert check.name == "memory"
        assert check.critical is False

    @pytest.mark.asyncio
    async def test_memory_health_check(self):
        """Should check memory usage."""
        check = create_memory_health_check(threshold_mb=10000)  # High threshold
        result = await check.execute()

        assert result.status == HealthStatus.HEALTHY

    def test_create_event_loop_health_check(self):
        """Should create event loop health check."""
        check = create_event_loop_health_check()

        assert check.name == "event_loop"
        assert check.critical is True

    @pytest.mark.asyncio
    async def test_event_loop_health_check(self):
        """Should check event loop responsiveness."""
        check = create_event_loop_health_check()
        result = await check.execute()

        assert result.status == HealthStatus.HEALTHY
