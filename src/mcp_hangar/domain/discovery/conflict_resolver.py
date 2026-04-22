"""Conflict Resolver for Discovery.

Resolves conflicts between static configuration and discovered mcp_servers,
as well as conflicts between multiple discovery sources.

Critical Design Decision: Static configuration ALWAYS wins over discovery.
This ensures explicit operator intent is never overridden by automated discovery.
"""

from dataclasses import dataclass
from enum import Enum

from ...logging_config import get_logger
from .discovered_mcp_server import DiscoveredMcpServer

logger = get_logger(__name__)


class ConflictResolution(Enum):
    """Resolution outcome for discovered mcp_servers."""

    STATIC_WINS = "static_wins"  # Static config takes precedence
    DISCOVERY_WINS = "discovery_wins"  # Never used, but defined for clarity
    SOURCE_PRIORITY = "source_priority"  # Higher priority source wins
    REGISTERED = "registered"  # New mcp_server registered
    REJECTED = "rejected"  # McpServer rejected
    UNCHANGED = "unchanged"  # McpServer unchanged, just update last_seen
    UPDATED = "updated"  # McpServer config changed, updating

    def __str__(self) -> str:
        return self.value


@dataclass
class ConflictResult:
    """Result of conflict resolution.

    Attributes:
        resolution: The type of resolution applied
        winner: The mcp_server that won (if any)
        reason: Human-readable explanation
    """

    resolution: ConflictResolution
    winner: DiscoveredMcpServer | None
    reason: str

    @property
    def should_register(self) -> bool:
        """Check if mcp_server should be registered."""
        return self.resolution in (
            ConflictResolution.REGISTERED,
            ConflictResolution.UPDATED,
            ConflictResolution.SOURCE_PRIORITY,
        )

    @property
    def should_update_seen(self) -> bool:
        """Check if last_seen should be updated."""
        return self.resolution in (
            ConflictResolution.UNCHANGED,
            ConflictResolution.REGISTERED,
            ConflictResolution.UPDATED,
        )


class ConflictResolver:
    """Resolves conflicts between static config and discovered mcp_servers.

    Resolution Rules:
        1. Static + Discovery conflict: Static wins. Discovery ignored. Warning logged.
        2. Multiple sources discover same name: First source wins (priority order).
        3. Discovery finds new mcp_server: Auto-register if mode=additive.
        4. McpServer disappears from source: If mode=authoritative, deregister after TTL.

    Source Priority (lower = higher priority):
        - static: 0 (Always wins)
        - kubernetes: 1
        - docker: 2
        - filesystem: 3
        - entrypoint: 4
    """

    # Source priority (lower number = higher priority)
    SOURCE_PRIORITY: dict[str, int] = {
        "static": 0,  # Always wins
        "kubernetes": 1,
        "docker": 2,
        "filesystem": 3,
        "entrypoint": 4,
    }

    def __init__(self, static_mcp_servers: set[str] | None = None):
        """Initialize conflict resolver.

        Args:
            static_mcp_servers: Set of mcp_server names from static config
        """
        self.static_mcp_servers = static_mcp_servers or set()
        self._registered: dict[str, DiscoveredMcpServer] = {}

    def add_static_mcp_server(self, name: str) -> None:
        """Add a mcp_server name to the static mcp_servers set.

        Args:
            name: McpServer name from static configuration
        """
        self.static_mcp_servers.add(name)

    def remove_static_mcp_server(self, name: str) -> None:
        """Remove a mcp_server name from the static mcp_servers set.

        Args:
            name: McpServer name to remove
        """
        self.static_mcp_servers.discard(name)

    def resolve(self, mcp_server: DiscoveredMcpServer) -> ConflictResult:
        """Determine if mcp_server should be registered.

        Args:
            mcp_server: Discovered mcp_server to resolve

        Returns:
            ConflictResult with resolution decision
        """
        # Rule 1: Static always wins
        if mcp_server.name in self.static_mcp_servers:
            logger.warning(
                f"McpServer '{mcp_server.name}' conflicts with static config. "
                f"Static wins. Discovery from {mcp_server.source_type} ignored."
            )
            return ConflictResult(
                resolution=ConflictResolution.STATIC_WINS,
                winner=None,
                reason="Static configuration takes precedence",
            )

        # Rule 2: Check existing registered mcp_servers
        existing = self._registered.get(mcp_server.name)
        if existing:
            # Same source, same fingerprint = no change
            if existing.source_type == mcp_server.source_type and existing.fingerprint == mcp_server.fingerprint:
                return ConflictResult(
                    resolution=ConflictResolution.UNCHANGED,
                    winner=mcp_server.with_updated_seen_time(),
                    reason="McpServer unchanged, updating last_seen",
                )

            # Same source, different fingerprint = config changed
            if existing.source_type == mcp_server.source_type:
                logger.info(
                    f"McpServer '{mcp_server.name}' config changed "
                    f"(fingerprint {existing.fingerprint} -> {mcp_server.fingerprint})"
                )
                return ConflictResult(
                    resolution=ConflictResolution.UPDATED,
                    winner=mcp_server,
                    reason="McpServer configuration updated",
                )

            # Different source = check priority
            existing_priority = self.SOURCE_PRIORITY.get(existing.source_type, 99)
            new_priority = self.SOURCE_PRIORITY.get(mcp_server.source_type, 99)

            if new_priority < existing_priority:
                logger.info(
                    f"McpServer '{mcp_server.name}' from {mcp_server.source_type} "
                    f"overrides {existing.source_type} (higher priority)"
                )
                return ConflictResult(
                    resolution=ConflictResolution.SOURCE_PRIORITY,
                    winner=mcp_server,
                    reason=f"{mcp_server.source_type} has higher priority than {existing.source_type}",
                )
            else:
                logger.debug(
                    f"McpServer '{mcp_server.name}' from {mcp_server.source_type} "
                    f"rejected (lower priority than {existing.source_type})"
                )
                return ConflictResult(
                    resolution=ConflictResolution.REJECTED,
                    winner=None,
                    reason=f"Existing source {existing.source_type} has higher priority",
                )

        # No conflict - new mcp_server
        logger.info(f"New mcp_server discovered: {mcp_server.name} from {mcp_server.source_type}")
        return ConflictResult(
            resolution=ConflictResolution.REGISTERED,
            winner=mcp_server,
            reason="New mcp_server registered",
        )

    def register(self, mcp_server: DiscoveredMcpServer) -> None:
        """Mark mcp_server as registered.

        Args:
            mcp_server: McpServer to register
        """
        self._registered[mcp_server.name] = mcp_server
        logger.debug(f"Registered mcp_server: {mcp_server.name}")

    def update(self, mcp_server: DiscoveredMcpServer) -> None:
        """Update registered mcp_server.

        Args:
            mcp_server: McpServer with updated configuration
        """
        self._registered[mcp_server.name] = mcp_server
        logger.debug(f"Updated mcp_server: {mcp_server.name}")

    def deregister(self, name: str) -> DiscoveredMcpServer | None:
        """Remove mcp_server from registry.

        Args:
            name: McpServer name to deregister

        Returns:
            The removed mcp_server, or None if not found
        """
        mcp_server = self._registered.pop(name, None)
        if mcp_server:
            logger.info(f"Deregistered mcp_server: {name}")
        return mcp_server

    def get_registered(self, name: str) -> DiscoveredMcpServer | None:
        """Get a registered mcp_server by name.

        Args:
            name: McpServer name

        Returns:
            The registered mcp_server, or None if not found
        """
        return self._registered.get(name)

    def get_all_registered(self) -> dict[str, DiscoveredMcpServer]:
        """Get all registered mcp_servers.

        Returns:
            Dictionary of name -> DiscoveredMcpServer
        """
        return dict(self._registered)

    def is_registered(self, name: str) -> bool:
        """Check if a mcp_server is registered.

        Args:
            name: McpServer name

        Returns:
            True if registered
        """
        return name in self._registered

    def get_source_priority(self, source_type: str) -> int:
        """Get priority for a source type.

        Args:
            source_type: Source type name

        Returns:
            Priority number (lower = higher priority)
        """
        return self.SOURCE_PRIORITY.get(source_type, 99)
