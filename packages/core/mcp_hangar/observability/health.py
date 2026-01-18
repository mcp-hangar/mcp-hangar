"""Health check endpoints and status for MCP Hangar.

Provides Kubernetes-compatible health endpoints:
- /health/live - Liveness probe (is the process alive?)
- /health/ready - Readiness probe (can it serve traffic?)
- /health/startup - Startup probe (has it finished initializing?)

Also provides detailed health status for dashboards.
"""

import asyncio
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)


class HealthStatus(Enum):
    """Health check result status."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class HealthCheckResult:
    """Result of a single health check."""

    name: str
    status: HealthStatus
    message: str = ""
    duration_ms: float = 0.0
    details: dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "duration_ms": round(self.duration_ms, 2),
            "details": self.details,
            "timestamp": self.timestamp,
        }


@dataclass
class HealthCheck:
    """Health check definition."""

    name: str
    check_fn: Callable[[], bool]
    description: str = ""
    timeout_seconds: float = 5.0
    critical: bool = True  # If False, failure degrades but doesn't make unhealthy

    async def execute(self) -> HealthCheckResult:
        """Execute the health check.

        Returns:
            HealthCheckResult with status and timing.
        """
        start = time.perf_counter()
        try:
            # Run check with timeout
            if asyncio.iscoroutinefunction(self.check_fn):
                result = await asyncio.wait_for(self.check_fn(), timeout=self.timeout_seconds)
            else:
                # Run sync function in thread pool
                loop = asyncio.get_event_loop()
                result = await asyncio.wait_for(
                    loop.run_in_executor(None, self.check_fn),
                    timeout=self.timeout_seconds,
                )

            duration_ms = (time.perf_counter() - start) * 1000

            if result:
                return HealthCheckResult(
                    name=self.name,
                    status=HealthStatus.HEALTHY,
                    message="Check passed",
                    duration_ms=duration_ms,
                )
            else:
                status = HealthStatus.UNHEALTHY if self.critical else HealthStatus.DEGRADED
                return HealthCheckResult(
                    name=self.name,
                    status=status,
                    message="Check returned false",
                    duration_ms=duration_ms,
                )

        except TimeoutError:
            duration_ms = (time.perf_counter() - start) * 1000
            status = HealthStatus.UNHEALTHY if self.critical else HealthStatus.DEGRADED
            return HealthCheckResult(
                name=self.name,
                status=status,
                message=f"Check timed out after {self.timeout_seconds}s",
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = (time.perf_counter() - start) * 1000
            status = HealthStatus.UNHEALTHY if self.critical else HealthStatus.DEGRADED
            return HealthCheckResult(
                name=self.name,
                status=status,
                message=f"Check failed: {str(e)}",
                duration_ms=duration_ms,
                details={"error_type": type(e).__name__},
            )


@dataclass
class HealthResponse:
    """Complete health response."""

    status: HealthStatus
    checks: list[HealthCheckResult]
    version: str = "unknown"
    uptime_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "status": self.status.value,
            "checks": [c.to_dict() for c in self.checks],
            "version": self.version,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "timestamp": time.time(),
        }


class HealthEndpoint:
    """Health endpoint manager.

    Manages health checks and provides Kubernetes-compatible endpoints.
    """

    def __init__(self):
        self._checks: list[HealthCheck] = []
        self._startup_complete = False
        self._start_time = time.time()
        self._lock = threading.Lock()
        self._last_results: dict[str, HealthCheckResult] = {}

    def register_check(self, check: HealthCheck) -> None:
        """Register a health check.

        Args:
            check: HealthCheck to register.
        """
        with self._lock:
            # Avoid duplicates
            existing_names = {c.name for c in self._checks}
            if check.name not in existing_names:
                self._checks.append(check)
                logger.debug("health_check_registered", name=check.name)

    def unregister_check(self, name: str) -> None:
        """Unregister a health check by name.

        Args:
            name: Name of check to remove.
        """
        with self._lock:
            self._checks = [c for c in self._checks if c.name != name]
            self._last_results.pop(name, None)

    def mark_startup_complete(self) -> None:
        """Mark that startup is complete."""
        self._startup_complete = True
        logger.info("startup_marked_complete")

    @property
    def uptime_seconds(self) -> float:
        """Get server uptime in seconds."""
        return time.time() - self._start_time

    async def check_liveness(self) -> HealthResponse:
        """Liveness probe - is the process alive?

        Always returns healthy unless completely broken.
        Used by Kubernetes to restart the container.

        Returns:
            HealthResponse with liveness status.
        """
        # Simple liveness - just verify we can respond
        return HealthResponse(
            status=HealthStatus.HEALTHY,
            checks=[
                HealthCheckResult(
                    name="liveness",
                    status=HealthStatus.HEALTHY,
                    message="Process is alive",
                )
            ],
            version=self._get_version(),
            uptime_seconds=self.uptime_seconds,
        )

    async def check_readiness(self) -> HealthResponse:
        """Readiness probe - can we serve traffic?

        Runs all registered health checks.
        Used by Kubernetes to route traffic.

        Returns:
            HealthResponse with aggregated status.
        """
        results = await self._run_all_checks()

        # Determine overall status
        has_unhealthy = any(r.status == HealthStatus.UNHEALTHY for r in results)
        has_degraded = any(r.status == HealthStatus.DEGRADED for r in results)

        if has_unhealthy:
            overall = HealthStatus.UNHEALTHY
        elif has_degraded:
            overall = HealthStatus.DEGRADED
        elif not results:
            overall = HealthStatus.HEALTHY  # No checks = healthy
        else:
            overall = HealthStatus.HEALTHY

        return HealthResponse(
            status=overall,
            checks=results,
            version=self._get_version(),
            uptime_seconds=self.uptime_seconds,
        )

    async def check_startup(self) -> HealthResponse:
        """Startup probe - has initialization completed?

        Returns unhealthy until mark_startup_complete() is called.
        Used by Kubernetes to delay liveness/readiness probes.

        Returns:
            HealthResponse with startup status.
        """
        if self._startup_complete:
            status = HealthStatus.HEALTHY
            message = "Startup complete"
        else:
            status = HealthStatus.UNHEALTHY
            message = "Startup in progress"

        return HealthResponse(
            status=status,
            checks=[
                HealthCheckResult(
                    name="startup",
                    status=status,
                    message=message,
                )
            ],
            version=self._get_version(),
            uptime_seconds=self.uptime_seconds,
        )

    async def get_detailed_status(self) -> dict[str, Any]:
        """Get detailed health status for dashboards.

        Returns:
            Detailed status dictionary.
        """
        results = await self._run_all_checks()

        # Calculate statistics
        total = len(results)
        healthy = sum(1 for r in results if r.status == HealthStatus.HEALTHY)
        degraded = sum(1 for r in results if r.status == HealthStatus.DEGRADED)
        unhealthy = sum(1 for r in results if r.status == HealthStatus.UNHEALTHY)

        avg_duration = sum(r.duration_ms for r in results) / total if total > 0 else 0

        return {
            "status": self._aggregate_status(results).value,
            "summary": {
                "total_checks": total,
                "healthy": healthy,
                "degraded": degraded,
                "unhealthy": unhealthy,
                "avg_check_duration_ms": round(avg_duration, 2),
            },
            "checks": [r.to_dict() for r in results],
            "version": self._get_version(),
            "uptime_seconds": round(self.uptime_seconds, 1),
            "startup_complete": self._startup_complete,
            "timestamp": time.time(),
        }

    async def _run_all_checks(self) -> list[HealthCheckResult]:
        """Run all registered health checks.

        Returns:
            List of check results.
        """
        with self._lock:
            checks = list(self._checks)

        if not checks:
            return []

        # Run checks concurrently
        tasks = [check.execute() for check in checks]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Process results
        processed = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                processed.append(
                    HealthCheckResult(
                        name=checks[i].name,
                        status=HealthStatus.UNHEALTHY,
                        message=f"Check execution failed: {result}",
                    )
                )
            else:
                processed.append(result)

        # Cache results
        with self._lock:
            for r in processed:
                self._last_results[r.name] = r

        return processed

    def _aggregate_status(self, results: list[HealthCheckResult]) -> HealthStatus:
        """Aggregate check results into overall status."""
        if not results:
            return HealthStatus.HEALTHY

        has_unhealthy = any(r.status == HealthStatus.UNHEALTHY for r in results)
        has_degraded = any(r.status == HealthStatus.DEGRADED for r in results)

        if has_unhealthy:
            return HealthStatus.UNHEALTHY
        elif has_degraded:
            return HealthStatus.DEGRADED
        else:
            return HealthStatus.HEALTHY

    def _get_version(self) -> str:
        """Get MCP Hangar version."""
        try:
            from mcp_hangar import __version__

            return __version__
        except (ImportError, AttributeError):
            return "unknown"

    def get_last_result(self, name: str) -> HealthCheckResult | None:
        """Get the last result for a specific check.

        Args:
            name: Check name.

        Returns:
            Last result or None.
        """
        with self._lock:
            return self._last_results.get(name)


# Global singleton
_health_endpoint: HealthEndpoint | None = None


def get_health_endpoint() -> HealthEndpoint:
    """Get the health endpoint singleton.

    Returns:
        HealthEndpoint instance.
    """
    global _health_endpoint
    if _health_endpoint is None:
        _health_endpoint = HealthEndpoint()
    return _health_endpoint


def reset_health_endpoint() -> None:
    """Reset health endpoint singleton (for testing)."""
    global _health_endpoint
    _health_endpoint = None


# Built-in health checks
def create_provider_health_check(providers_dict: Any) -> HealthCheck:
    """Create health check for provider availability.

    Args:
        providers_dict: Providers dictionary or dict-like object.

    Returns:
        HealthCheck instance.
    """

    def check() -> bool:
        if not providers_dict:
            return True  # No providers = healthy (vacuous)

        total = len(providers_dict)
        ready = sum(1 for p in providers_dict.values() if hasattr(p, "state") and str(p.state) == "ready")

        # At least 50% providers should be ready
        return total == 0 or (ready / total) >= 0.5

    return HealthCheck(
        name="providers",
        check_fn=check,
        description="Check that at least 50% of providers are ready",
        critical=False,  # Degraded, not unhealthy
    )


def create_memory_health_check(
    threshold_mb: int = 1024,
) -> HealthCheck:
    """Create health check for memory usage.

    Args:
        threshold_mb: Memory threshold in MB.

    Returns:
        HealthCheck instance.
    """

    def check() -> bool:
        try:
            import resource

            # Get current memory usage (RSS in bytes)
            usage = resource.getrusage(resource.RUSAGE_SELF)
            rss_mb = usage.ru_maxrss / (1024 * 1024)  # Convert to MB

            # macOS reports in bytes, Linux in KB
            import platform

            if platform.system() == "Darwin":
                rss_mb = usage.ru_maxrss / (1024 * 1024)
            else:
                rss_mb = usage.ru_maxrss / 1024

            return rss_mb < threshold_mb
        except (ImportError, AttributeError):
            return True  # Can't check, assume healthy

    return HealthCheck(
        name="memory",
        check_fn=check,
        description=f"Check memory usage is below {threshold_mb}MB",
        critical=False,
    )


def create_event_loop_health_check() -> HealthCheck:
    """Create health check for event loop responsiveness.

    Returns:
        HealthCheck instance.
    """

    async def check() -> bool:
        # Simple check that async works
        await asyncio.sleep(0.001)
        return True

    return HealthCheck(
        name="event_loop",
        check_fn=check,
        description="Check event loop is responsive",
        timeout_seconds=1.0,
        critical=True,
    )
