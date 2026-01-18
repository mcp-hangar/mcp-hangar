"""Discovery Lifecycle Manager.

Manages the lifecycle of discovered providers including TTL tracking,
quarantine management, and graceful deregistration.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Optional

from mcp_hangar.domain.discovery.discovered_provider import DiscoveredProvider

from ...logging_config import get_logger

logger = get_logger(__name__)


# Type alias for registry callback
RegistryCallback = Callable[[str, str], Awaitable[None]]


class DiscoveryLifecycleManager:
    """Manages lifecycle of discovered providers.

    Responsibilities:
        - Track provider TTLs and expiration
        - Manage quarantine state
        - Handle graceful deregistration
        - Provide manual approval workflow

    Usage:
        manager = DiscoveryLifecycleManager(default_ttl=90)
        manager.add_provider(provider)

        # Periodic check
        expired = await manager.check_expirations()
    """

    def __init__(
        self,
        default_ttl: int = 90,
        check_interval: int = 10,
        drain_timeout: int = 30,
        on_deregister: Optional[RegistryCallback] = None,
    ):
        """Initialize lifecycle manager.

        Args:
            default_ttl: Default TTL in seconds (3x refresh interval)
            check_interval: Interval between expiration checks
            drain_timeout: Timeout for graceful connection draining
            on_deregister: Callback when provider should be deregistered
        """
        self.default_ttl = default_ttl
        self.check_interval = check_interval
        self.drain_timeout = drain_timeout
        self.on_deregister = on_deregister

        # Active providers
        self._providers: dict[str, DiscoveredProvider] = {}

        # Quarantined providers: name -> (provider, reason, timestamp)
        self._quarantine: dict[str, tuple[DiscoveredProvider, str, datetime]] = {}

        # Providers being drained (graceful shutdown)
        self._draining: set[str] = set()

        # Lifecycle task
        self._running = False
        self._lifecycle_task: Optional[asyncio.Task] = None

    async def start(self) -> None:
        """Start lifecycle management loop."""
        if self._running:
            return

        self._running = True
        self._lifecycle_task = asyncio.create_task(self._lifecycle_loop())
        logger.info(f"Lifecycle manager started (ttl={self.default_ttl}s, interval={self.check_interval}s)")

    async def stop(self) -> None:
        """Stop lifecycle management."""
        self._running = False

        if self._lifecycle_task:
            self._lifecycle_task.cancel()
            try:
                await self._lifecycle_task
            except asyncio.CancelledError:
                pass
            self._lifecycle_task = None

        logger.info("Lifecycle manager stopped")

    async def _lifecycle_loop(self) -> None:
        """Periodic check for expired providers."""
        while self._running:
            try:
                await self._check_expirations()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in lifecycle loop: {e}")
                await asyncio.sleep(self.check_interval)

    async def _check_expirations(self) -> list[str]:
        """Check and handle expired providers.

        Returns:
            List of expired provider names
        """
        expired = []

        for name, provider in list(self._providers.items()):
            if provider.is_expired():
                expired.append(name)
                logger.info(f"Provider '{name}' expired (last seen: {provider.last_seen_at}). Starting deregistration.")
                await self._deregister(name, "ttl_expired")

        return expired

    async def _deregister(self, name: str, reason: str) -> None:
        """Deregister a provider with optional draining.

        Args:
            name: Provider name
            reason: Reason for deregistration
        """
        if name in self._draining:
            return

        provider = self._providers.pop(name, None)
        if not provider:
            return

        # Mark as draining
        self._draining.add(name)

        try:
            # Callback to main registry
            if self.on_deregister:
                await self.on_deregister(name, reason)
        except Exception as e:
            logger.error(f"Error deregistering provider {name}: {e}")
        finally:
            self._draining.discard(name)

    def add_provider(self, provider: DiscoveredProvider) -> None:
        """Add provider to lifecycle tracking.

        Args:
            provider: Provider to track
        """
        self._providers[provider.name] = provider
        logger.debug(f"Added provider to lifecycle tracking: {provider.name}")

    def update_seen(self, name: str) -> Optional[DiscoveredProvider]:
        """Update last_seen for a provider.

        Args:
            name: Provider name

        Returns:
            Updated provider, or None if not found
        """
        if name in self._providers:
            old_provider = self._providers[name]
            updated = old_provider.with_updated_seen_time()
            self._providers[name] = updated
            return updated
        return None

    def update_provider(self, provider: DiscoveredProvider) -> None:
        """Update provider configuration.

        Args:
            provider: Updated provider
        """
        self._providers[provider.name] = provider
        logger.debug(f"Updated provider in lifecycle tracking: {provider.name}")

    def remove_provider(self, name: str) -> Optional[DiscoveredProvider]:
        """Remove provider from tracking.

        Args:
            name: Provider name

        Returns:
            Removed provider, or None if not found
        """
        return self._providers.pop(name, None)

    def get_provider(self, name: str) -> Optional[DiscoveredProvider]:
        """Get a tracked provider.

        Args:
            name: Provider name

        Returns:
            Provider, or None if not found
        """
        return self._providers.get(name)

    def get_all_providers(self) -> dict[str, DiscoveredProvider]:
        """Get all tracked providers.

        Returns:
            Dictionary of name -> provider
        """
        return dict(self._providers)

    # Quarantine management

    def quarantine(self, provider: DiscoveredProvider, reason: str) -> None:
        """Move provider to quarantine.

        Args:
            provider: Provider to quarantine
            reason: Reason for quarantine
        """
        self._quarantine[provider.name] = (provider, reason, datetime.now(UTC))
        # Remove from active tracking
        self._providers.pop(provider.name, None)
        logger.warning(f"Provider '{provider.name}' quarantined: {reason}")

    def approve(self, name: str) -> Optional[DiscoveredProvider]:
        """Approve quarantined provider for registration.

        Args:
            name: Provider name

        Returns:
            Approved provider, or None if not in quarantine
        """
        if name in self._quarantine:
            provider, reason, _ = self._quarantine.pop(name)
            # Add back to active tracking
            self._providers[provider.name] = provider
            logger.info(f"Approved quarantined provider: {name}")
            return provider
        return None

    def reject(self, name: str) -> Optional[DiscoveredProvider]:
        """Reject and remove quarantined provider.

        Args:
            name: Provider name

        Returns:
            Rejected provider, or None if not in quarantine
        """
        if name in self._quarantine:
            provider, _, _ = self._quarantine.pop(name)
            logger.info(f"Rejected quarantined provider: {name}")
            return provider
        return None

    def get_quarantined(self) -> dict[str, tuple[DiscoveredProvider, str, datetime]]:
        """Get all quarantined providers.

        Returns:
            Dictionary of name -> (provider, reason, quarantine_time)
        """
        return dict(self._quarantine)

    def is_quarantined(self, name: str) -> bool:
        """Check if provider is quarantined.

        Args:
            name: Provider name

        Returns:
            True if quarantined
        """
        return name in self._quarantine

    # Stats and status

    def get_stats(self) -> dict[str, int]:
        """Get lifecycle statistics.

        Returns:
            Dictionary with counts
        """
        return {
            "active": len(self._providers),
            "quarantined": len(self._quarantine),
            "draining": len(self._draining),
        }

    def get_expiring_soon(self, threshold_seconds: int = 30) -> list[DiscoveredProvider]:
        """Get providers expiring soon.

        Args:
            threshold_seconds: Time threshold for "soon"

        Returns:
            List of providers expiring within threshold
        """
        expiring = []
        now = datetime.now(UTC)

        for provider in self._providers.values():
            last_seen = provider.last_seen_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)

            elapsed = (now - last_seen).total_seconds()
            remaining = provider.ttl_seconds - elapsed

            if remaining <= threshold_seconds:
                expiring.append(provider)

        return expiring
