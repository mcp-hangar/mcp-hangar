"""Discovery Lifecycle Manager.

Manages the lifecycle of discovered mcp_servers including TTL tracking,
quarantine management, and graceful deregistration.
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, UTC

from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer

from ...logging_config import get_logger

logger = get_logger(__name__)


# Type alias for hangar callback
HangarCallback = Callable[[str, str], Awaitable[None]]


class DiscoveryLifecycleManager:
    """Manages lifecycle of discovered mcp_servers.

    Responsibilities:
        - Track mcp_server TTLs and expiration
        - Manage quarantine state
        - Handle graceful deregistration
        - Provide manual approval workflow

    Usage:
        manager = DiscoveryLifecycleManager(default_ttl=90)
        manager.add_mcp_server(mcp_server)

        # Periodic check
        expired = await manager.check_expirations()
    """

    def __init__(
        self,
        default_ttl: int = 90,
        check_interval: int = 10,
        drain_timeout: int = 30,
        on_deregister: HangarCallback | None = None,
    ):
        """Initialize lifecycle manager.

        Args:
            default_ttl: Default TTL in seconds (3x refresh interval)
            check_interval: Interval between expiration checks
            drain_timeout: Timeout for graceful connection draining
            on_deregister: Callback when mcp_server should be deregistered
        """
        self.default_ttl = default_ttl
        self.check_interval = check_interval
        self.drain_timeout = drain_timeout
        self.on_deregister = on_deregister

        # Active mcp_servers
        self._mcp_servers: dict[str, DiscoveredMcpServer] = {}

        # Quarantined mcp_servers: name -> (mcp_server, reason, timestamp)
        self._quarantine: dict[str, tuple[DiscoveredMcpServer, str, datetime]] = {}

        # McpServers being drained (graceful shutdown)
        self._draining: set[str] = set()

        # Lifecycle task
        self._running = False
        self._lifecycle_task: asyncio.Task | None = None

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
        """Periodic check for expired mcp_servers."""
        while self._running:
            try:
                await self._check_expirations()
                await asyncio.sleep(self.check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:  # noqa: BLE001 -- fault-barrier: lifecycle check error must not crash background loop
                logger.error(f"Error in lifecycle loop: {e}")
                await asyncio.sleep(self.check_interval)

    async def _check_expirations(self) -> list[str]:
        """Check and handle expired mcp_servers.

        Returns:
            List of expired mcp_server names
        """
        expired = []

        for name, mcp_server in list(self._mcp_servers.items()):
            if mcp_server.is_expired():
                expired.append(name)
                logger.info(
                    f"McpServer '{name}' expired (last seen: {mcp_server.last_seen_at}). Starting deregistration."
                )
                await self._deregister(name, "ttl_expired")

        return expired

    async def _deregister(self, name: str, reason: str) -> None:
        """Deregister a mcp_server with optional draining.

        Args:
            name: McpServer name
            reason: Reason for deregistration
        """
        if name in self._draining:
            return

        mcp_server = self._mcp_servers.pop(name, None)
        if not mcp_server:
            return

        # Mark as draining
        self._draining.add(name)

        try:
            # Callback to main registry
            if self.on_deregister:
                await self.on_deregister(name, reason)
        except Exception as e:  # noqa: BLE001 -- fault-barrier: deregister callback failure must not crash lifecycle
            logger.error(f"Error deregistering mcp_server {name}: {e}")
        finally:
            self._draining.discard(name)

    def add_mcp_server(self, mcp_server: DiscoveredMcpServer) -> None:
        """Add mcp_server to lifecycle tracking.

        Args:
            mcp_server: McpServer to track
        """
        self._mcp_servers[mcp_server.name] = mcp_server
        logger.debug(f"Added mcp_server to lifecycle tracking: {mcp_server.name}")

    def update_seen(self, name: str) -> DiscoveredMcpServer | None:
        """Update last_seen for a mcp_server.

        Args:
            name: McpServer name

        Returns:
            Updated mcp_server, or None if not found
        """
        if name in self._mcp_servers:
            old_mcp_server = self._mcp_servers[name]
            updated = old_mcp_server.with_updated_seen_time()
            self._mcp_servers[name] = updated
            return updated
        return None

    def update_mcp_server(self, mcp_server: DiscoveredMcpServer) -> None:
        """Update mcp_server configuration.

        Args:
            mcp_server: Updated mcp_server
        """
        self._mcp_servers[mcp_server.name] = mcp_server
        logger.debug(f"Updated mcp_server in lifecycle tracking: {mcp_server.name}")

    def remove_mcp_server(self, name: str) -> DiscoveredMcpServer | None:
        """Remove mcp_server from tracking.

        Args:
            name: McpServer name

        Returns:
            Removed mcp_server, or None if not found
        """
        return self._mcp_servers.pop(name, None)

    def get_mcp_server(self, name: str) -> DiscoveredMcpServer | None:
        """Get a tracked mcp_server.

        Args:
            name: McpServer name

        Returns:
            McpServer, or None if not found
        """
        return self._mcp_servers.get(name)

    def get_all_mcp_servers(self) -> dict[str, DiscoveredMcpServer]:
        """Get all tracked mcp_servers.

        Returns:
            Dictionary of name -> mcp_server
        """
        return dict(self._mcp_servers)

    # Quarantine management

    def quarantine(self, mcp_server: DiscoveredMcpServer, reason: str) -> None:
        """Move mcp_server to quarantine.

        Args:
            mcp_server: McpServer to quarantine
            reason: Reason for quarantine
        """
        self._quarantine[mcp_server.name] = (mcp_server, reason, datetime.now(UTC))
        # Remove from active tracking
        self._mcp_servers.pop(mcp_server.name, None)
        logger.warning(f"McpServer '{mcp_server.name}' quarantined: {reason}")

    def approve(self, name: str) -> DiscoveredMcpServer | None:
        """Approve quarantined mcp_server for registration.

        Args:
            name: McpServer name

        Returns:
            Approved mcp_server, or None if not in quarantine
        """
        if name in self._quarantine:
            mcp_server, reason, _ = self._quarantine.pop(name)
            # Add back to active tracking
            self._mcp_servers[mcp_server.name] = mcp_server
            logger.info(f"Approved quarantined mcp_server: {name}")
            return mcp_server
        return None

    def reject(self, name: str) -> DiscoveredMcpServer | None:
        """Reject and remove quarantined mcp_server.

        Args:
            name: McpServer name

        Returns:
            Rejected mcp_server, or None if not in quarantine
        """
        if name in self._quarantine:
            mcp_server, _, _ = self._quarantine.pop(name)
            logger.info(f"Rejected quarantined mcp_server: {name}")
            return mcp_server
        return None

    def get_quarantined(self) -> dict[str, tuple[DiscoveredMcpServer, str, datetime]]:
        """Get all quarantined mcp_servers.

        Returns:
            Dictionary of name -> (mcp_server, reason, quarantine_time)
        """
        return dict(self._quarantine)

    def is_quarantined(self, name: str) -> bool:
        """Check if mcp_server is quarantined.

        Args:
            name: McpServer name

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
            "active": len(self._mcp_servers),
            "quarantined": len(self._quarantine),
            "draining": len(self._draining),
        }

    def get_expiring_soon(self, threshold_seconds: int = 30) -> list[DiscoveredMcpServer]:
        """Get mcp_servers expiring soon.

        Args:
            threshold_seconds: Time threshold for "soon"

        Returns:
            List of mcp_servers expiring within threshold
        """
        expiring = []
        now = datetime.now(UTC)

        for mcp_server in self._mcp_servers.values():
            last_seen = mcp_server.last_seen_at
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=UTC)

            elapsed = (now - last_seen).total_seconds()
            remaining = mcp_server.ttl_seconds - elapsed

            if remaining <= threshold_seconds:
                expiring.append(mcp_server)

        return expiring
