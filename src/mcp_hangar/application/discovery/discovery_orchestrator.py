"""Discovery Orchestrator.

Main coordination component for mcp_server discovery.
Manages discovery sources, validation, and integration with the registry.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, UTC
from typing import TYPE_CHECKING, Any

from mcp_hangar.domain.discovery.conflict_resolver import ConflictResolver
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_service import DiscoveryCycleResult, DiscoveryService
from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
from mcp_hangar.logging_config import get_logger
from mcp_hangar.observability.tracing import get_tracer

if TYPE_CHECKING:
    from mcp_hangar.domain.security.input_validator import InputValidator

# Import main metrics for unified observability
from mcp_hangar import metrics as main_metrics

from .discovery_metrics import get_discovery_metrics
from .lifecycle_manager import DiscoveryLifecycleManager
from .security_validator import SecurityConfig, SecurityValidator

logger = get_logger(__name__)


@dataclass
class DiscoveryConfig:
    """Configuration for discovery orchestrator.

    Attributes:
        enabled: Master switch for discovery
        refresh_interval_s: Interval between discovery cycles
        auto_register: Whether to auto-register discovered mcp_servers
        security: Security configuration
        lifecycle: Lifecycle configuration
    """

    enabled: bool = True
    refresh_interval_s: int = 30
    auto_register: bool = True

    # Security settings
    security: SecurityConfig = field(default_factory=SecurityConfig)

    # Lifecycle settings
    default_ttl_s: int = 90
    check_interval_s: int = 10
    drain_timeout_s: int = 30

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DiscoveryConfig:
        """Create from dictionary (e.g., from config.yaml).

        Args:
            data: Configuration dictionary

        Returns:
            DiscoveryConfig instance
        """
        security_data = data.get("security", {})
        lifecycle_data = data.get("lifecycle", {})

        return cls(
            enabled=data.get("enabled", True),
            refresh_interval_s=data.get("refresh_interval_s", 30),
            auto_register=data.get("auto_register", True),
            security=SecurityConfig.from_dict(security_data),
            default_ttl_s=lifecycle_data.get("default_ttl_s", 90),
            check_interval_s=lifecycle_data.get("check_interval_s", 10),
            drain_timeout_s=lifecycle_data.get("drain_timeout_s", 30),
        )


# Type for registry registration callback
RegistrationCallback = Callable[[DiscoveredMcpServer], Awaitable[bool]]
DeregistrationCallback = Callable[[str, str], Awaitable[None]]


class DiscoveryOrchestrator:
    """Main coordination component for mcp_server discovery.

    Orchestrates:
        - Multiple discovery sources
        - Security validation pipeline
        - Lifecycle management (TTL, quarantine)
        - Integration with main registry
        - Metrics and observability

    Usage:
        orchestrator = DiscoveryOrchestrator(config)
        orchestrator.add_source(KubernetesDiscoverySource())
        orchestrator.add_source(DockerDiscoverySource())

        # Set callbacks for registry integration
        orchestrator.on_register = async_register_fn
        orchestrator.on_deregister = async_deregister_fn

        # Start discovery
        await orchestrator.start()
    """

    def __init__(
        self,
        config: DiscoveryConfig | None = None,
        static_mcp_servers: set[str] | None = None,
        input_validator: InputValidator | None = None,
    ):
        """Initialize discovery orchestrator.

        Args:
            config: Discovery configuration
            static_mcp_servers: Set of static mcp_server names (from config)
            input_validator: Optional InputValidator for command validation
        """
        self.config = config or DiscoveryConfig()
        self._input_validator = input_validator

        # Core components
        self._conflict_resolver = ConflictResolver(static_mcp_servers)
        self._discovery_service = DiscoveryService(
            conflict_resolver=self._conflict_resolver,
            auto_register=self.config.auto_register,
        )
        self._validator = SecurityValidator(self.config.security)
        self._lifecycle_manager = DiscoveryLifecycleManager(
            default_ttl=self.config.default_ttl_s,
            check_interval=self.config.check_interval_s,
            drain_timeout=self.config.drain_timeout_s,
        )
        self._metrics = get_discovery_metrics()

        # Callbacks for registry integration
        self.on_register: RegistrationCallback | None = None
        self.on_deregister: DeregistrationCallback | None = None

        # Discovery loop state
        self._running = False
        self._discovery_task: asyncio.Task[None] | None = None
        self._last_cycle: datetime | None = None

    def add_source(self, source: DiscoverySource) -> None:
        """Add a discovery source.

        Args:
            source: Discovery source to add
        """
        self._discovery_service.register_source(source)
        logger.info(f"Added discovery source: {source.source_type}")

    def remove_source(self, source_type: str) -> DiscoverySource | None:
        """Remove a discovery source.

        Args:
            source_type: Type of source to remove

        Returns:
            Removed source, or None if not found
        """
        return self._discovery_service.unregister_source(source_type)

    def set_static_mcp_servers(self, names: set[str]) -> None:
        """Set static mcp_server names (from config).

        Args:
            names: Set of static mcp_server names
        """
        self._discovery_service.set_static_mcp_servers(names)

    async def start(self) -> None:
        """Start the discovery orchestrator."""
        if not self.config.enabled:
            logger.info("Discovery is disabled in configuration")
            return

        if self._running:
            logger.warning("Discovery orchestrator already running")
            return

        self._running = True

        # Set up lifecycle manager callback
        self._lifecycle_manager.on_deregister = self._handle_deregister

        # Start components
        await self._discovery_service.start()
        await self._lifecycle_manager.start()

        # Start discovery loop
        self._discovery_task = asyncio.create_task(self._discovery_loop())

        logger.info(f"Discovery orchestrator started (refresh_interval={self.config.refresh_interval_s}s)")

    async def stop(self) -> None:
        """Stop the discovery orchestrator."""
        self._running = False

        # Cancel discovery loop
        if self._discovery_task:
            self._discovery_task.cancel()
            try:
                await self._discovery_task
            except asyncio.CancelledError:
                pass
            self._discovery_task = None

        # Stop components
        await self._lifecycle_manager.stop()
        await self._discovery_service.stop()

        logger.info("Discovery orchestrator stopped")

    async def _discovery_loop(self) -> None:
        """Main discovery loop."""
        # Initial discovery
        await self.run_discovery_cycle()

        while self._running:
            try:
                await asyncio.sleep(self.config.refresh_interval_s)
                if self._running:
                    await self.run_discovery_cycle()
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 -- fault-barrier: discovery loop error must not crash background task
                logger.error(f"Error in discovery loop: {e}")
                self._metrics.inc_errors(source="orchestrator", error_type=type(e).__name__)

    async def run_discovery_cycle(self) -> DiscoveryCycleResult:
        """Run a single discovery cycle.

        Returns:
            DiscoveryCycleResult with cycle statistics
        """
        import time

        start_time = time.perf_counter()

        result = DiscoveryCycleResult()
        tracer = get_tracer(__name__)

        with tracer.start_as_current_span("discovery.cycle") as cycle_span:
            try:
                # Run discovery on all sources
                cycle_result = await self._discovery_service.run_discovery_cycle()
                result.discovered_count = cycle_result.discovered_count
                result.source_results = cycle_result.source_results
                cycle_span.set_attribute("discovery.discovered_count", result.discovered_count)

                # Process discovered mcp_servers through validation
                for mcp_server in self._discovery_service.get_registered_mcp_servers().values():
                    validation_result = await self._process_mcp_server(mcp_server)

                    if validation_result == "registered":
                        result.registered_count += 1
                    elif validation_result == "updated":
                        result.updated_count += 1
                    elif validation_result == "quarantined":
                        result.quarantined_count += 1

                # Check for deregistrations
                result.deregistered_count = cycle_result.deregistered_count
                result.error_count = cycle_result.error_count

            except Exception as e:  # noqa: BLE001 -- fault-barrier: cycle failure must not crash orchestrator
                logger.error(f"Discovery cycle failed: {e}")
                result.error_count += 1
                self._metrics.inc_errors(source="orchestrator", error_type=type(e).__name__)
                cycle_span.record_exception(e)

            # Calculate duration
            duration_seconds = time.perf_counter() - start_time
            result.duration_ms = duration_seconds * 1000

            cycle_span.set_attribute("discovery.registered_count", result.registered_count)
            cycle_span.set_attribute("discovery.quarantined_count", result.quarantined_count)
            cycle_span.set_attribute("discovery.error_count", result.error_count)
            cycle_span.set_attribute("discovery.duration_ms", round(result.duration_ms, 2))

        # Update internal metrics
        self._metrics.observe_cycle_duration(duration_seconds)
        self._last_cycle = datetime.now(UTC)

        # Update main metrics for unified observability
        for source in self._discovery_service.get_all_sources():
            source_count = result.source_results.get(source.source_type, 0)
            main_metrics.record_discovery_cycle(
                source_type=source.source_type,
                duration=duration_seconds,
                discovered=source_count,
                registered=result.registered_count,
                quarantined=result.quarantined_count,
            )

        logger.debug(
            f"Discovery cycle complete: {result.discovered_count} discovered, "
            f"{result.registered_count} registered in {result.duration_ms:.2f}ms"
        )

        return result

    async def _process_mcp_server(self, mcp_server: DiscoveredMcpServer) -> str:
        """Process a discovered mcp_server through validation.

        Args:
            mcp_server: McpServer to process

        Returns:
            Status string: "registered", "updated", "quarantined", "skipped", "rejected"
        """
        # Check if already tracked
        existing = self._lifecycle_manager.get_mcp_server(mcp_server.name)
        if existing:
            if existing.fingerprint == mcp_server.fingerprint:
                # Just update last_seen
                self._lifecycle_manager.update_seen(mcp_server.name)
                return "skipped"
            else:
                # Config changed, need to validate again
                pass

        tracer = get_tracer(__name__)
        with tracer.start_as_current_span("discovery.process_mcp_server") as prov_span:
            prov_span.set_attribute("discovery.mcp_server_name", mcp_server.name)
            prov_span.set_attribute("discovery.source_type", mcp_server.source_type)

            # Validate command from untrusted discovery sources
            command = mcp_server.connection_info.get("command", [])
            if command and self._input_validator:
                try:
                    validation_result = self._input_validator.validate_command(command)
                except ValueError as exc:
                    logger.warning(
                        "discovered_mcp_server_command_rejected",
                        mcp_server_name=mcp_server.name,
                        source=mcp_server.source_type,
                        command=command,
                        reason=str(exc),
                    )
                    prov_span.set_attribute("discovery.result", "rejected")
                    return "rejected"

                if not validation_result.valid:
                    issues = "; ".join(i.message for i in validation_result.issues)
                    logger.warning(
                        "discovered_mcp_server_command_rejected",
                        mcp_server_name=mcp_server.name,
                        source=mcp_server.source_type,
                        command=command,
                        reason=issues,
                    )
                    prov_span.set_attribute("discovery.result", "rejected")
                    return "rejected"

            # Validate mcp_server
            validation_report = await self._validator.validate(mcp_server)

            self._metrics.observe_validation_duration(
                source=mcp_server.source_type,
                duration_seconds=validation_report.duration_ms / 1000,
            )
            prov_span.set_attribute("discovery.validation_passed", validation_report.is_passed)

            if not validation_report.is_passed:
                # Handle validation failure
                logger.warning(f"McpServer '{mcp_server.name}' failed validation: {validation_report.reason}")

                self._metrics.inc_validation_failures(
                    source=mcp_server.source_type,
                    validation_type=validation_report.result.value,
                )

                if self.config.security.quarantine_on_failure:
                    self._lifecycle_manager.quarantine(mcp_server, validation_report.reason)
                    self._metrics.inc_quarantine(reason=validation_report.result.value)
                    main_metrics.record_discovery_quarantine(reason=validation_report.result.value)
                    prov_span.set_attribute("discovery.result", "quarantined")
                    return "quarantined"

                prov_span.set_attribute("discovery.result", "skipped")
                return "skipped"

            # Register with main registry
            if self.on_register:
                try:
                    success = await self.on_register(mcp_server)
                    if not success:
                        logger.warning(f"Control plane rejected mcp_server: {mcp_server.name}")
                        prov_span.set_attribute("discovery.result", "skipped")
                        return "skipped"
                except Exception as e:  # noqa: BLE001 -- fault-barrier: registration callback failure must not crash discovery
                    logger.error(f"Error registering mcp_server {mcp_server.name}: {e}")
                    prov_span.set_attribute("discovery.result", "skipped")
                    prov_span.record_exception(e)
                    return "skipped"

            # Track in lifecycle manager
            if existing:
                self._lifecycle_manager.update_mcp_server(mcp_server)
                self._metrics.inc_registrations(source=mcp_server.source_type)
                prov_span.set_attribute("discovery.result", "updated")
                return "updated"
            else:
                self._lifecycle_manager.add_mcp_server(mcp_server)
                self._validator.record_registration(mcp_server)
                self._metrics.inc_registrations(source=mcp_server.source_type)
                prov_span.set_attribute("discovery.result", "registered")
                return "registered"

    async def _handle_deregister(self, name: str, reason: str) -> None:
        """Handle mcp_server deregistration.

        Args:
            name: McpServer name
            reason: Reason for deregistration
        """
        mcp_server = self._lifecycle_manager.get_mcp_server(name)
        if mcp_server:
            self._validator.record_deregistration(mcp_server)
            self._metrics.inc_deregistrations(source=mcp_server.source_type, reason=reason)
            main_metrics.record_discovery_deregistration(source_type=mcp_server.source_type, reason=reason)

        if self.on_deregister:
            try:
                await self.on_deregister(name, reason)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: deregister callback failure must not crash lifecycle
                logger.error(f"Error in deregister callback for {name}: {e}")

    # Public API for tools

    async def trigger_discovery(self) -> dict[str, Any]:
        """Trigger immediate discovery cycle.

        Returns:
            Discovery results
        """
        result = await self.run_discovery_cycle()
        return result.to_dict()

    def get_pending_mcp_servers(self) -> list[DiscoveredMcpServer]:
        """Get mcp_servers pending registration.

        Returns:
            List of pending mcp_servers
        """
        return self._discovery_service.get_pending_mcp_servers()

    def get_quarantined(self) -> dict[str, dict[str, Any]]:
        """Get quarantined mcp_servers with reasons.

        Returns:
            Dictionary of name -> {mcp_server, reason, quarantine_time}
        """
        quarantined = self._lifecycle_manager.get_quarantined()
        return {
            name: {
                "mcp_server": mcp_server.to_dict(),
                "reason": reason,
                "quarantine_time": qtime.isoformat(),
            }
            for name, (mcp_server, reason, qtime) in quarantined.items()
        }

    async def approve_mcp_server(self, name: str) -> dict[str, Any]:
        """Approve a quarantined mcp_server.

        Args:
            name: McpServer name

        Returns:
            Result dictionary
        """
        mcp_server = self._lifecycle_manager.approve(name)

        if mcp_server:
            # Register with main registry
            if self.on_register:
                try:
                    await self.on_register(mcp_server)
                except Exception as e:  # noqa: BLE001 -- fault-barrier: registration callback failure must not crash approval
                    logger.error(f"Error registering approved mcp_server {name}: {e}")
                    return {"approved": False, "mcp_server": name, "error": str(e)}

            self._validator.record_registration(mcp_server)
            self._metrics.inc_registrations(source=mcp_server.source_type)

            return {"approved": True, "mcp_server": name, "status": "registered"}

        return {
            "approved": False,
            "mcp_server": name,
            "error": "McpServer not found in quarantine",
        }

    async def reject_mcp_server(self, name: str) -> dict[str, Any]:
        """Reject a quarantined mcp_server.

        Args:
            name: McpServer name

        Returns:
            Result dictionary
        """
        mcp_server = self._lifecycle_manager.reject(name)

        if mcp_server:
            return {"rejected": True, "mcp_server": name}

        return {
            "rejected": False,
            "mcp_server": name,
            "error": "McpServer not found in quarantine",
        }

    async def get_sources_status(self) -> list[dict[str, Any]]:
        """Get status of all discovery sources.

        Returns:
            List of source status dictionaries
        """
        statuses = await self._discovery_service.get_sources_status()

        # Update main metrics for each source
        for status in statuses:
            main_metrics.update_discovery_source(
                source_type=status.source_type,
                mode=status.mode.value,
                is_healthy=status.is_healthy,
                mcp_servers_count=status.mcp_servers_count,
            )

        return [s.to_dict() for s in statuses]

    def get_stats(self) -> dict[str, Any]:
        """Get orchestrator statistics.

        Returns:
            Statistics dictionary
        """
        lifecycle_stats = self._lifecycle_manager.get_stats()

        return {
            "enabled": self.config.enabled,
            "running": self._running,
            "last_cycle": self._last_cycle.isoformat() if self._last_cycle else None,
            "refresh_interval_s": self.config.refresh_interval_s,
            "sources_count": len(self._discovery_service.get_all_sources()),
            **lifecycle_stats,
        }
