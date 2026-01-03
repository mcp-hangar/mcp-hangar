"""Discovery Domain Service.

Coordinates provider discovery across multiple sources
and applies business rules for registration and lifecycle management.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Set
import logging

from .discovered_provider import DiscoveredProvider
from .discovery_source import DiscoveryMode, DiscoverySource
from .conflict_resolver import ConflictResolver, ConflictResolution

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryCycleResult:
    """Result of a discovery cycle.

    Attributes:
        discovered_count: Number of providers discovered
        registered_count: Number of new providers registered
        updated_count: Number of providers updated
        deregistered_count: Number of providers deregistered
        quarantined_count: Number of providers quarantined
        error_count: Number of errors during discovery
        duration_ms: Duration of the cycle in milliseconds
        source_results: Results per source
    """

    discovered_count: int = 0
    registered_count: int = 0
    updated_count: int = 0
    deregistered_count: int = 0
    quarantined_count: int = 0
    error_count: int = 0
    duration_ms: float = 0.0
    source_results: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "discovered_count": self.discovered_count,
            "registered_count": self.registered_count,
            "updated_count": self.updated_count,
            "deregistered_count": self.deregistered_count,
            "quarantined_count": self.quarantined_count,
            "error_count": self.error_count,
            "duration_ms": self.duration_ms,
            "source_results": self.source_results,
        }


@dataclass
class SourceStatus:
    """Status of a discovery source.

    Attributes:
        source_type: Type of the source
        mode: Discovery mode (additive/authoritative)
        is_healthy: Whether the source is healthy
        is_enabled: Whether the source is enabled
        last_discovery: Timestamp of last discovery
        providers_count: Number of providers from this source
        error_message: Last error message (if any)
    """

    source_type: str
    mode: DiscoveryMode
    is_healthy: bool
    is_enabled: bool
    last_discovery: Optional[datetime] = None
    providers_count: int = 0
    error_message: Optional[str] = None

    def to_dict(self) -> Dict:
        """Convert to dictionary for serialization."""
        return {
            "source_type": self.source_type,
            "mode": self.mode.value,
            "is_healthy": self.is_healthy,
            "is_enabled": self.is_enabled,
            "last_discovery": self.last_discovery.isoformat() if self.last_discovery else None,
            "providers_count": self.providers_count,
            "error_message": self.error_message,
        }


class DiscoveryService:
    """Domain service for provider discovery.

    This service coordinates multiple discovery sources and applies
    business rules for provider registration, conflict resolution,
    and lifecycle management.

    Responsibilities:
        - Register and manage discovery sources
        - Run discovery cycles across all sources
        - Resolve conflicts using ConflictResolver
        - Track source health and status
        - Manage pending and quarantined providers
    """

    def __init__(self, conflict_resolver: Optional[ConflictResolver] = None, auto_register: bool = True):
        """Initialize discovery service.

        Args:
            conflict_resolver: Resolver for handling conflicts
            auto_register: Whether to auto-register discovered providers
        """
        self._sources: Dict[str, DiscoverySource] = {}
        self._conflict_resolver = conflict_resolver or ConflictResolver()
        self._auto_register = auto_register

        # Track providers by source
        self._providers_by_source: Dict[str, Set[str]] = {}

        # Pending providers (discovered but not registered)
        self._pending: Dict[str, DiscoveredProvider] = {}

        # Quarantined providers (failed validation)
        self._quarantine: Dict[str, tuple[DiscoveredProvider, str]] = {}

        # Source status tracking
        self._source_status: Dict[str, SourceStatus] = {}

    def register_source(self, source: DiscoverySource) -> None:
        """Register a discovery source.

        Args:
            source: Discovery source to register
        """
        source_type = source.source_type
        if source_type in self._sources:
            logger.warning(f"Replacing existing source: {source_type}")

        self._sources[source_type] = source
        self._providers_by_source[source_type] = set()
        self._source_status[source_type] = SourceStatus(
            source_type=source_type, mode=source.mode, is_healthy=False, is_enabled=source.is_enabled
        )

        logger.info(f"Registered discovery source: {source_type} (mode={source.mode})")

    def unregister_source(self, source_type: str) -> Optional[DiscoverySource]:
        """Unregister a discovery source.

        Args:
            source_type: Type of source to unregister

        Returns:
            The unregistered source, or None if not found
        """
        source = self._sources.pop(source_type, None)
        if source:
            self._providers_by_source.pop(source_type, None)
            self._source_status.pop(source_type, None)
            logger.info(f"Unregistered discovery source: {source_type}")
        return source

    def get_source(self, source_type: str) -> Optional[DiscoverySource]:
        """Get a registered source by type.

        Args:
            source_type: Type of source

        Returns:
            The source, or None if not found
        """
        return self._sources.get(source_type)

    def get_all_sources(self) -> List[DiscoverySource]:
        """Get all registered sources.

        Returns:
            List of registered sources
        """
        return list(self._sources.values())

    async def run_discovery_cycle(self) -> DiscoveryCycleResult:
        """Run a discovery cycle across all sources.

        This method:
        1. Runs discovery on all enabled sources
        2. Resolves conflicts using ConflictResolver
        3. Handles provider registration/deregistration
        4. Updates source status and metrics

        Returns:
            DiscoveryCycleResult with cycle statistics
        """
        import time

        start_time = time.perf_counter()

        result = DiscoveryCycleResult()
        all_discovered: Dict[str, DiscoveredProvider] = {}

        # Run discovery on all enabled sources
        for source_type, source in self._sources.items():
            if not source.is_enabled:
                continue

            try:
                providers = await source.discover()
                result.source_results[source_type] = len(providers)

                # Update source status
                self._source_status[source_type].is_healthy = True
                self._source_status[source_type].last_discovery = datetime.now(timezone.utc)
                self._source_status[source_type].providers_count = len(providers)
                self._source_status[source_type].error_message = None

                # Track providers from this source
                current_names = set()

                for provider in providers:
                    result.discovered_count += 1
                    current_names.add(provider.name)

                    # Resolve conflicts
                    conflict_result = self._conflict_resolver.resolve(provider)

                    if conflict_result.should_register:
                        if conflict_result.resolution == ConflictResolution.REGISTERED:
                            result.registered_count += 1
                        elif conflict_result.resolution == ConflictResolution.UPDATED:
                            result.updated_count += 1

                        if self._auto_register and conflict_result.winner:
                            self._conflict_resolver.register(conflict_result.winner)
                            all_discovered[provider.name] = conflict_result.winner
                        elif conflict_result.winner:
                            self._pending[provider.name] = conflict_result.winner

                    elif conflict_result.should_update_seen and conflict_result.winner:
                        # Just update last_seen
                        self._conflict_resolver.update(conflict_result.winner)
                        all_discovered[provider.name] = conflict_result.winner

                # Handle authoritative mode - deregister missing providers
                if source.mode == DiscoveryMode.AUTHORITATIVE:
                    previous_names = self._providers_by_source.get(source_type, set())
                    lost_names = previous_names - current_names

                    for name in lost_names:
                        existing = self._conflict_resolver.get_registered(name)
                        if existing and existing.source_type == source_type:
                            # Check if expired
                            if existing.is_expired():
                                self._conflict_resolver.deregister(name)
                                result.deregistered_count += 1
                                logger.info(f"Deregistered expired provider: {name}")

                # Update tracked providers for this source
                self._providers_by_source[source_type] = current_names

            except Exception as e:
                logger.error(f"Discovery failed for source {source_type}: {e}")
                result.error_count += 1
                result.source_results[source_type] = 0

                # Update source status
                self._source_status[source_type].is_healthy = False
                self._source_status[source_type].error_message = str(e)

        # Calculate duration
        result.duration_ms = (time.perf_counter() - start_time) * 1000

        logger.info(
            f"Discovery cycle complete: {result.discovered_count} discovered, "
            f"{result.registered_count} registered, {result.updated_count} updated, "
            f"{result.deregistered_count} deregistered in {result.duration_ms:.2f}ms"
        )

        return result

    async def discover_from_source(self, source_type: str) -> List[DiscoveredProvider]:
        """Run discovery from a single source.

        Args:
            source_type: Type of source to discover from

        Returns:
            List of discovered providers

        Raises:
            ValueError: If source not found
        """
        source = self._sources.get(source_type)
        if not source:
            raise ValueError(f"Source not found: {source_type}")

        return await source.discover()

    async def get_sources_status(self) -> List[SourceStatus]:
        """Get status of all discovery sources.

        Returns:
            List of SourceStatus objects
        """
        # Update health status
        for source_type, source in self._sources.items():
            try:
                is_healthy = await source.health_check()
                self._source_status[source_type].is_healthy = is_healthy
                self._source_status[source_type].is_enabled = source.is_enabled
            except Exception as e:
                self._source_status[source_type].is_healthy = False
                self._source_status[source_type].error_message = str(e)

        return list(self._source_status.values())

    def get_pending_providers(self) -> List[DiscoveredProvider]:
        """Get providers pending registration.

        Returns:
            List of pending providers
        """
        return list(self._pending.values())

    def approve_pending(self, name: str) -> Optional[DiscoveredProvider]:
        """Approve a pending provider for registration.

        Args:
            name: Provider name to approve

        Returns:
            The approved provider, or None if not found
        """
        provider = self._pending.pop(name, None)
        if provider:
            self._conflict_resolver.register(provider)
            logger.info(f"Approved pending provider: {name}")
        return provider

    def quarantine(self, provider: DiscoveredProvider, reason: str) -> None:
        """Move a provider to quarantine.

        Args:
            provider: Provider to quarantine
            reason: Reason for quarantine
        """
        self._quarantine[provider.name] = (provider, reason)
        logger.warning(f"Quarantined provider '{provider.name}': {reason}")

    def approve_quarantined(self, name: str) -> Optional[DiscoveredProvider]:
        """Approve a quarantined provider for registration.

        Args:
            name: Provider name to approve

        Returns:
            The approved provider, or None if not found
        """
        if name in self._quarantine:
            provider, _ = self._quarantine.pop(name)
            self._conflict_resolver.register(provider)
            logger.info(f"Approved quarantined provider: {name}")
            return provider
        return None

    def get_quarantined(self) -> Dict[str, tuple[DiscoveredProvider, str]]:
        """Get all quarantined providers with reasons.

        Returns:
            Dictionary of name -> (provider, reason)
        """
        return dict(self._quarantine)

    def get_registered_providers(self) -> Dict[str, DiscoveredProvider]:
        """Get all registered providers.

        Returns:
            Dictionary of name -> DiscoveredProvider
        """
        return self._conflict_resolver.get_all_registered()

    def set_static_providers(self, names: Set[str]) -> None:
        """Set the static providers (from config).

        Args:
            names: Set of static provider names
        """
        for name in names:
            self._conflict_resolver.add_static_provider(name)

    async def start(self) -> None:
        """Start all discovery sources."""
        for source in self._sources.values():
            try:
                await source.start()
            except Exception as e:
                logger.error(f"Failed to start source {source.source_type}: {e}")

    async def stop(self) -> None:
        """Stop all discovery sources."""
        for source in self._sources.values():
            try:
                await source.stop()
            except Exception as e:
                logger.error(f"Failed to stop source {source.source_type}: {e}")
