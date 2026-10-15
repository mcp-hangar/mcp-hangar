"""Security Validator for Discovery.

Validates discovered mcp_servers before registration.
Implements a multi-stage validation pipeline with security controls.

Validation Pipeline:
    1. Source Validation - Is the source trusted?
    2. Rate Limit Check - Is this source flooding?
    3. Health Check - Does the mcp_server respond?
    4. Schema Validation - Does it implement MCP correctly?
"""

import json
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any

from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoverySource

from ..._sdk_compat import LATEST_PROTOCOL_VERSION
from ...logging_config import get_logger

logger = get_logger(__name__)

# Optional aiohttp dependency
try:
    import aiohttp

    AIOHTTP_AVAILABLE = True
except ImportError:
    AIOHTTP_AVAILABLE = False
    # Note: No logging here - module is imported before setup_logging() is called


class ValidationResult(Enum):
    """Result of validation pipeline."""

    PASSED = "passed"
    FAILED_SOURCE = "failed_source"
    FAILED_HEALTH = "failed_health"
    FAILED_SCHEMA = "failed_schema"
    FAILED_RATE_LIMIT = "failed_rate_limit"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        return self.value

    @property
    def is_passed(self) -> bool:
        return self in (ValidationResult.PASSED, ValidationResult.SKIPPED)


@dataclass
class ValidationReport:
    """Report from validation pipeline.

    Attributes:
        result: Validation result
        mcp_server: McpServer being validated
        reason: Human-readable explanation
        details: Additional details (URLs, errors, etc.)
        duration_ms: Validation duration in milliseconds
    """

    result: ValidationResult
    mcp_server: DiscoveredMcpServer
    reason: str
    details: dict[str, Any] | None = None
    duration_ms: float = 0.0

    @property
    def is_passed(self) -> bool:
        return self.result.is_passed

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        return {
            "result": self.result.value,
            "mcp_server_name": self.mcp_server.name,
            "reason": self.reason,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


@dataclass
class SecurityConfig:
    """Security configuration for validation.

    Holds the checks that are true of every source. Rules only one kind of
    infrastructure understands belong to that source's `policy_violation`.

    Attributes:
        require_health_check: Whether to require health check pass
        require_mcp_schema: Whether to validate MCP schema
        max_mcp_servers_per_source: Max mcp_servers from single source
        max_registration_rate: Max registrations per minute per source
        health_check_timeout_s: Health check timeout in seconds
        quarantine_on_failure: Whether to quarantine failed mcp_servers
    """

    require_health_check: bool = True
    require_mcp_schema: bool = False
    max_mcp_servers_per_source: int = 100
    max_registration_rate: int = 10  # per minute
    health_check_timeout_s: float = 5.0
    quarantine_on_failure: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SecurityConfig":
        """Create from dictionary."""
        return cls(
            require_health_check=data.get("require_health_check", True),
            require_mcp_schema=data.get("require_mcp_schema", False),
            max_mcp_servers_per_source=data.get("max_mcp_servers_per_source", 100),
            max_registration_rate=data.get("max_registration_rate", 10),
            health_check_timeout_s=data.get("health_check_timeout_s", 5.0),
            quarantine_on_failure=data.get("quarantine_on_failure", True),
        )


class SecurityValidator:
    """Validates discovered mcp_servers before registration.

    Implements a multi-stage validation pipeline:
        1. Source Validation - Namespace whitelist/blacklist
        2. Rate Limit Check - Prevent registration floods
        3. Health Check - Verify mcp_server is responsive
        4. Schema Validation - Verify MCP compliance

    Usage:
        validator = SecurityValidator(config)
        report = await validator.validate(mcp_server)
        if report.is_passed:
            # Register mcp_server
        else:
            # Quarantine or reject
    """

    def __init__(self, config: SecurityConfig | None = None):
        """Initialize security validator.

        Args:
            config: Security configuration
        """
        self.config = config or SecurityConfig()

        # Rate limiting state: source -> list of timestamps
        self._registration_counts: dict[str, list[float]] = {}

        # McpServer counts per source
        self._mcp_server_counts: dict[str, int] = {}

    async def validate(
        self, mcp_server: DiscoveredMcpServer, source: "DiscoverySource | None" = None
    ) -> ValidationReport:
        """Run full validation pipeline.

        Args:
            mcp_server: McpServer to validate

        Returns:
            ValidationReport with result and details
        """
        start_time = time.perf_counter()

        # Step 1: Source validation
        source_result = self._validate_source(mcp_server, source)
        if source_result:
            source_result.duration_ms = (time.perf_counter() - start_time) * 1000
            return source_result

        # Step 2: Rate limit check
        rate_result = self._check_rate_limit(mcp_server)
        if rate_result:
            rate_result.duration_ms = (time.perf_counter() - start_time) * 1000
            return rate_result

        # Step 3: McpServer count check
        count_result = self._check_mcp_server_count(mcp_server)
        if count_result:
            count_result.duration_ms = (time.perf_counter() - start_time) * 1000
            return count_result

        # Step 4: Health check (for HTTP mcp_servers)
        if self.config.require_health_check:
            health_result = await self._validate_health(mcp_server)
            if health_result:
                health_result.duration_ms = (time.perf_counter() - start_time) * 1000
                return health_result

        # Step 5: MCP schema validation
        if self.config.require_mcp_schema:
            schema_result = await self._validate_schema(mcp_server)
            if schema_result:
                schema_result.duration_ms = (time.perf_counter() - start_time) * 1000
                return schema_result

        # All checks passed
        duration_ms = (time.perf_counter() - start_time) * 1000
        return ValidationReport(
            result=ValidationResult.PASSED,
            mcp_server=mcp_server,
            reason="All validation checks passed",
            duration_ms=duration_ms,
        )

    def _validate_source(
        self, mcp_server: DiscoveredMcpServer, source: "DiscoverySource | None" = None
    ) -> ValidationReport | None:
        """Ask the source whether it objects to what it found.

        This used to branch on the source's name and then apply namespace rules
        read from this module's own config -- a security component that knew
        which sources exist, and a set of constraints only one of them could
        ever satisfy. Every new source then had two bad options: pass these
        checks vacuously, or have its author edit security code.

        The source answers for its own world now. The core keeps what is true of
        every source -- rate, count, health, schema -- and learns nothing about
        namespaces, projects or datacenters.

        Args:
            mcp_server: The discovered server.
            source: The source that produced it, when the caller knows which.
                Absent in unit tests of the generic checks, where there is no
                source-specific policy to apply.

        Returns:
            A failed report if the source objects, None otherwise.
        """
        if source is None:
            return None

        violation = source.policy_violation(mcp_server)
        if violation is None:
            return None

        return ValidationReport(
            result=ValidationResult.FAILED_SOURCE,
            mcp_server=mcp_server,
            reason=violation.reason,
            details=dict(violation.details),
        )

    def _check_rate_limit(self, mcp_server: DiscoveredMcpServer) -> ValidationReport | None:
        """Check registration rate limit.

        Args:
            mcp_server: McpServer to validate

        Returns:
            ValidationReport if rate exceeded, None if within limit
        """
        source = mcp_server.source_type
        now = time.time()
        window = 60.0  # 1 minute window

        # Initialize if needed
        if source not in self._registration_counts:
            self._registration_counts[source] = []

        # Clean old entries
        self._registration_counts[source] = [t for t in self._registration_counts[source] if now - t < window]

        # Check rate
        if len(self._registration_counts[source]) >= self.config.max_registration_rate:
            return ValidationReport(
                result=ValidationResult.FAILED_RATE_LIMIT,
                mcp_server=mcp_server,
                reason=f"Rate limit exceeded for source '{source}'",
                details={
                    "source": source,
                    "current_rate": len(self._registration_counts[source]),
                    "max_rate": self.config.max_registration_rate,
                    "window_seconds": window,
                },
            )

        # Record this registration attempt
        self._registration_counts[source].append(now)
        return None

    def _check_mcp_server_count(self, mcp_server: DiscoveredMcpServer) -> ValidationReport | None:
        """Check mcp_server count per source.

        Args:
            mcp_server: McpServer to validate

        Returns:
            ValidationReport if count exceeded, None if within limit
        """
        source = mcp_server.source_type
        current_count = self._mcp_server_counts.get(source, 0)

        if current_count >= self.config.max_mcp_servers_per_source:
            return ValidationReport(
                result=ValidationResult.FAILED_RATE_LIMIT,
                mcp_server=mcp_server,
                reason=f"Max mcp_servers exceeded for source '{source}'",
                details={
                    "source": source,
                    "current_count": current_count,
                    "max_count": self.config.max_mcp_servers_per_source,
                },
            )

        return None

    async def _validate_health(self, mcp_server: DiscoveredMcpServer) -> ValidationReport | None:
        """Validate mcp_server health endpoint.

        Args:
            mcp_server: McpServer to validate

        Returns:
            ValidationReport if health check failed, None if passed
        """
        # Only check HTTP-based mcp_servers
        if mcp_server.mode not in ("http", "sse", "remote"):
            return None

        if not AIOHTTP_AVAILABLE:
            logger.debug(f"Skipping health check for {mcp_server.name} (aiohttp not available)")
            return None

        host = mcp_server.connection_info.get("host")
        port = mcp_server.connection_info.get("port")
        health_path = mcp_server.connection_info.get("health_path", "/health")

        if not host or not port:
            return ValidationReport(
                result=ValidationResult.FAILED_HEALTH,
                mcp_server=mcp_server,
                reason="Missing host or port in connection_info",
                details={"connection_info": mcp_server.connection_info},
            )

        url = f"http://{host}:{port}{health_path}"

        try:
            timeout = aiohttp.ClientTimeout(total=self.config.health_check_timeout_s)

            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as response:
                    status = response.status
                if status == 404:
                    # No health endpoint is not a sick server: many MCP servers
                    # serve only their MCP path. Every MCP server answers
                    # `initialize`, so ask that instead (#1756). 404 only -- a 5xx
                    # or a timeout is a real failure and is not second-guessed.
                    return await self._validate_initialize(session, mcp_server, url)
                if status != 200:
                    return ValidationReport(
                        result=ValidationResult.FAILED_HEALTH,
                        mcp_server=mcp_server,
                        reason=f"Health check returned status {status}",
                        details={"url": url, "status": status},
                    )

        except TimeoutError:
            return ValidationReport(
                result=ValidationResult.FAILED_HEALTH,
                mcp_server=mcp_server,
                reason="Health check timed out",
                details={"url": url, "timeout": self.config.health_check_timeout_s},
            )
        except Exception as e:  # noqa: BLE001 -- fault-barrier: health check failure returns validation report, not crash
            return ValidationReport(
                result=ValidationResult.FAILED_HEALTH,
                mcp_server=mcp_server,
                reason=f"Health check failed: {e}",
                details={"url": url, "error": str(e)},
            )

        return None

    async def _validate_initialize(
        self, session: Any, mcp_server: DiscoveredMcpServer, health_url: str
    ) -> ValidationReport | None:
        """Pass a server whose health path is absent if its MCP path answers `initialize`.

        Returns:
            ValidationReport naming both probes if `initialize` got no JSON-RPC
            result, None if it did.
        """
        host = mcp_server.connection_info.get("host")
        port = mcp_server.connection_info.get("port")
        path = mcp_server.connection_info.get("path") or "/mcp"
        url = f"http://{host}:{port}{path}"
        request = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LATEST_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mcp-hangar-discovery", "version": "1"},
            },
        }
        headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
        details: dict[str, Any] = {"health_url": health_url, "health_status": 404, "url": url}
        async with session.post(url, json=request, headers=headers) as response:
            details["status"] = response.status
            body = await response.text()
        if details["status"] == 200 and _has_jsonrpc_result(body):
            logger.debug("discovery_health_passed_on_initialize", mcp_server=mcp_server.name, url=url)
            return None
        return ValidationReport(
            result=ValidationResult.FAILED_HEALTH,
            mcp_server=mcp_server,
            reason=(
                f"Health check returned status 404, and initialize at {path} returned "
                f"status {details['status']} without a JSON-RPC result"
            ),
            details=details,
        )

    async def _validate_schema(self, mcp_server: DiscoveredMcpServer) -> ValidationReport | None:
        """Validate MCP tools schema.

        Args:
            mcp_server: McpServer to validate

        Returns:
            ValidationReport if schema invalid, None if valid
        """
        # NOTE: MCP schema validation is intentionally deferred.
        # The mcp_server's tools/list response should be validated against MCP spec,
        # but this requires network calls during registration which adds latency.
        # Schema validation can be done lazily on first tool invocation instead.
        logger.debug(f"Schema validation deferred for {mcp_server.name}")
        return None

    def record_registration(self, mcp_server: DiscoveredMcpServer) -> None:
        """Record successful registration for counting.

        Args:
            mcp_server: Registered mcp_server
        """
        source = mcp_server.source_type
        self._mcp_server_counts[source] = self._mcp_server_counts.get(source, 0) + 1

    def record_deregistration(self, mcp_server: DiscoveredMcpServer) -> None:
        """Record deregistration for counting.

        Args:
            mcp_server: Deregistered mcp_server
        """
        source = mcp_server.source_type
        if source in self._mcp_server_counts:
            self._mcp_server_counts[source] = max(0, self._mcp_server_counts[source] - 1)

    def reset_rate_limits(self) -> None:
        """Reset all rate limit counters."""
        self._registration_counts.clear()

    def reset_mcp_server_counts(self) -> None:
        """Reset all mcp_server counts."""
        self._mcp_server_counts.clear()


def _has_jsonrpc_result(body: str) -> bool:
    """Whether an `initialize` answer carries a JSON-RPC result, as JSON or as SSE."""
    candidates = [body]
    candidates += [line[len("data:") :].strip() for line in body.splitlines() if line.startswith("data:")]
    for candidate in candidates:
        try:
            message = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(message, dict) and isinstance(message.get("result"), dict):
            return True
    return False
