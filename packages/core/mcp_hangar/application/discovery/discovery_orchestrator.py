"""Discovery Orchestrator.

Main coordination component for provider discovery.
Manages discovery sources, validation, and integration with the registry.
"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional

# Import main metrics for unified observability
from mcp_hangar import metrics as main_metrics
from mcp_hangar.domain.discovery.conflict_resolver import ConflictResolver
from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider
from mcp_hangar.domain.discovery.discovery_service import DiscoveryCycleResult, DiscoveryService
from mcp_hangar.domain.discovery.discovery_source import DiscoverySource
from mcp_hangar.logging_config import get_logger

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
        auto_register: Whether to auto-register discovered providers
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
    def from_dict(cls, data: dict[str, Any]) -> "DiscoveryConfig":
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
RegistrationCallback = Callable[[DiscoveredProvider], Awaitable[bool]]
DeregistrationCallback = Callable[[str, str], Awaitable[None]]


class DiscoveryOrchestrator:
    """Main coordination component for provider discovery.

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
        config: Optional[DiscoveryConfig] = None,
        static_providers: Optional[set[str]] = None,
    ):
        """Initialize discovery orchestrator.

        Args:
            config: Discovery configuration
            static_providers: Set of static provider names (from config)
        """
        self.config = config or DiscoveryConfig()

        # Core components
        self._conflict_resolver = ConflictResolver(static_providers)
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
        self.on_register: Optional[RegistrationCallback] = None
        self.on_deregister: Optional[DeregistrationCallback] = None

        # Discovery loop state
        self._running = False
        self._discovery_task: Optional[asyncio.Task] = None
        self._last_cycle: Optional[datetime] = None

    def add_source(self, source: DiscoverySource) -> None:
        """Add a discovery source.

        Args:
            source: Discovery source to add
        """
        self._discovery_service.register_source(source)
        logger.info(f"Added discovery source: {source.source_type}")

    def remove_source(self, source_type: str) -> Optional[DiscoverySource]:
        """Remove a discovery source.

        Args:
            source_type: Type of source to remove

        Returns:
            Removed source, or None if not found
        """
        return self._discovery_service.unregister_source(source_type)

    def set_static_providers(self, names: set[str]) -> None:
        """Set static provider names (from config).

        Args:
            names: Set of static provider names
        """
        self._discovery_service.set_static_providers(names)

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
            except Exception as e:
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

        try:
            # Run discovery on all sources
            cycle_result = await self._discovery_service.run_discovery_cycle()
            result.discovered_count = cycle_result.discovered_count
            result.source_results = cycle_result.source_results

            # Process discovered providers through validation
            for provider in self._discovery_service.get_registered_providers().values():
                validation_result = await self._process_provider(provider)

                if validation_result == "registered":
                    result.registered_count += 1
                elif validation_result == "updated":
                    result.updated_count += 1
                elif validation_result == "quarantined":
                    result.quarantined_count += 1

            # Check for deregistrations
            result.deregistered_count = cycle_result.deregistered_count
            result.error_count = cycle_result.error_count

        except Exception as e:
            logger.error(f"Discovery cycle failed: {e}")
            result.error_count += 1
            self._metrics.inc_errors(source="orchestrator", error_type=type(e).__name__)

        # Calculate duration
        duration_seconds = time.perf_counter() - start_time
        result.duration_ms = duration_seconds * 1000

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

    async def _process_provider(self, provider: DiscoveredProvider) -> str:
        """Process a discovered provider through validation.

        Args:
            provider: Provider to process

        Returns:
            Status string: "registered", "updated", "quarantined", "skipped"
        """
        # Check if already tracked
        existing = self._lifecycle_manager.get_provider(provider.name)
        if existing:
            if existing.fingerprint == provider.fingerprint:
                # Just update last_seen
                self._lifecycle_manager.update_seen(provider.name)
                return "skipped"
            else:
                # Config changed, need to validate again
                pass

        # Validate provider
        validation_report = await self._validator.validate(provider)

        self._metrics.observe_validation_duration(
            source=provider.source_type,
            duration_seconds=validation_report.duration_ms / 1000,
        )

        if not validation_report.is_passed:
            # Handle validation failure
            logger.warning(f"Provider '{provider.name}' failed validation: {validation_report.reason}")

            self._metrics.inc_validation_failures(
                source=provider.source_type,
                validation_type=validation_report.result.value,
            )

            if self.config.security.quarantine_on_failure:
                self._lifecycle_manager.quarantine(provider, validation_report.reason)
                self._metrics.inc_quarantine(reason=validation_report.result.value)
                main_metrics.record_discovery_quarantine(reason=validation_report.result.value)
                return "quarantined"

            return "skipped"

        # Register with main registry
        if self.on_register:
            try:
                success = await self.on_register(provider)
                if not success:
                    logger.warning(f"Registry rejected provider: {provider.name}")
                    return "skipped"
            except Exception as e:
                logger.error(f"Error registering provider {provider.name}: {e}")
                return "skipped"

        # Track in lifecycle manager
        if existing:
            self._lifecycle_manager.update_provider(provider)
            self._metrics.inc_registrations(source=provider.source_type)
            return "updated"
        else:
            self._lifecycle_manager.add_provider(provider)
            self._validator.record_registration(provider)
            self._metrics.inc_registrations(source=provider.source_type)
            return "registered"

    async def _handle_deregister(self, name: str, reason: str) -> None:
        """Handle provider deregistration.

        Args:
            name: Provider name
            reason: Reason for deregistration
        """
        provider = self._lifecycle_manager.get_provider(name)
        if provider:
            self._validator.record_deregistration(provider)
            self._metrics.inc_deregistrations(source=provider.source_type, reason=reason)
            main_metrics.record_discovery_deregistration(source_type=provider.source_type, reason=reason)

        if self.on_deregister:
            try:
                await self.on_deregister(name, reason)
            except Exception as e:
                logger.error(f"Error in deregister callback for {name}: {e}")

    # Public API for tools

    async def trigger_discovery(self) -> dict[str, Any]:
        """Trigger immediate discovery cycle.

        Returns:
            Discovery results
        """
        result = await self.run_discovery_cycle()
        return result.to_dict()

    def get_pending_providers(self) -> list[DiscoveredProvider]:
        """Get providers pending registration.

        Returns:
            List of pending providers
        """
        return self._discovery_service.get_pending_providers()

    def get_quarantined(self) -> dict[str, dict[str, Any]]:
        """Get quarantined providers with reasons.

        Returns:
            Dictionary of name -> {provider, reason, quarantine_time}
        """
        quarantined = self._lifecycle_manager.get_quarantined()
        return {
            name: {
                "provider": provider.to_dict(),
                "reason": reason,
                "quarantine_time": qtime.isoformat(),
            }
            for name, (provider, reason, qtime) in quarantined.items()
        }

    async def approve_provider(self, name: str) -> dict[str, Any]:
        """Approve a quarantined provider.

        Args:
            name: Provider name

        Returns:
            Result dictionary
        """
        provider = self._lifecycle_manager.approve(name)

        if provider:
            # Register with main registry
            if self.on_register:
                try:
                    await self.on_register(provider)
                except Exception as e:
                    logger.error(f"Error registering approved provider {name}: {e}")
                    return {"approved": False, "provider": name, "error": str(e)}

            self._validator.record_registration(provider)
            self._metrics.inc_registrations(source=provider.source_type)

            return {"approved": True, "provider": name, "status": "registered"}

        return {
            "approved": False,
            "provider": name,
            "error": "Provider not found in quarantine",
        }

    async def reject_provider(self, name: str) -> dict[str, Any]:
        """Reject a quarantined provider.

        Args:
            name: Provider name

        Returns:
            Result dictionary
        """
        provider = self._lifecycle_manager.reject(name)

        if provider:
            return {"rejected": True, "provider": name}

        return {
            "rejected": False,
            "provider": name,
            "error": "Provider not found in quarantine",
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
                mode=status.mode,
                is_healthy=status.is_healthy,
                providers_count=status.providers_count,
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
