"""Conflict Resolver for Discovery.

Resolves conflicts between static configuration and discovered providers,
as well as conflicts between multiple discovery sources.

Critical Design Decision: Static configuration ALWAYS wins over discovery.
This ensures explicit operator intent is never overridden by automated discovery.
"""

from dataclasses import dataclass
from enum import Enum

from ...logging_config import get_logger
from .discovered_provider import DiscoveredProvider

logger = get_logger(__name__)


class ConflictResolution(Enum):
    """Resolution outcome for discovered providers."""

    STATIC_WINS = "static_wins"  # Static config takes precedence
    DISCOVERY_WINS = "discovery_wins"  # Never used, but defined for clarity
    SOURCE_PRIORITY = "source_priority"  # Higher priority source wins
    REGISTERED = "registered"  # New provider registered
    REJECTED = "rejected"  # Provider rejected
    UNCHANGED = "unchanged"  # Provider unchanged, just update last_seen
    UPDATED = "updated"  # Provider config changed, updating

    def __str__(self) -> str:
        return self.value


@dataclass
class ConflictResult:
    """Result of conflict resolution.

    Attributes:
        resolution: The type of resolution applied
        winner: The provider that won (if any)
        reason: Human-readable explanation
    """

    resolution: ConflictResolution
    winner: DiscoveredProvider | None
    reason: str

    @property
    def should_register(self) -> bool:
        """Check if provider should be registered."""
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
    """Resolves conflicts between static config and discovered providers.

    Resolution Rules:
        1. Static + Discovery conflict: Static wins. Discovery ignored. Warning logged.
        2. Multiple sources discover same name: First source wins (priority order).
        3. Discovery finds new provider: Auto-register if mode=additive.
        4. Provider disappears from source: If mode=authoritative, deregister after TTL.

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

    def __init__(self, static_providers: set[str] | None = None):
        """Initialize conflict resolver.

        Args:
            static_providers: Set of provider names from static config
        """
        self.static_providers = static_providers or set()
        self._registered: dict[str, DiscoveredProvider] = {}

    def add_static_provider(self, name: str) -> None:
        """Add a provider name to the static providers set.

        Args:
            name: Provider name from static configuration
        """
        self.static_providers.add(name)

    def remove_static_provider(self, name: str) -> None:
        """Remove a provider name from the static providers set.

        Args:
            name: Provider name to remove
        """
        self.static_providers.discard(name)

    def resolve(self, provider: DiscoveredProvider) -> ConflictResult:
        """Determine if provider should be registered.

        Args:
            provider: Discovered provider to resolve

        Returns:
            ConflictResult with resolution decision
        """
        # Rule 1: Static always wins
        if provider.name in self.static_providers:
            logger.warning(
                f"Provider '{provider.name}' conflicts with static config. "
                f"Static wins. Discovery from {provider.source_type} ignored."
            )
            return ConflictResult(
                resolution=ConflictResolution.STATIC_WINS,
                winner=None,
                reason="Static configuration takes precedence",
            )

        # Rule 2: Check existing registered providers
        existing = self._registered.get(provider.name)
        if existing:
            # Same source, same fingerprint = no change
            if existing.source_type == provider.source_type and existing.fingerprint == provider.fingerprint:
                return ConflictResult(
                    resolution=ConflictResolution.UNCHANGED,
                    winner=provider.with_updated_seen_time(),
                    reason="Provider unchanged, updating last_seen",
                )

            # Same source, different fingerprint = config changed
            if existing.source_type == provider.source_type:
                logger.info(
                    f"Provider '{provider.name}' config changed "
                    f"(fingerprint {existing.fingerprint} -> {provider.fingerprint})"
                )
                return ConflictResult(
                    resolution=ConflictResolution.UPDATED,
                    winner=provider,
                    reason="Provider configuration updated",
                )

            # Different source = check priority
            existing_priority = self.SOURCE_PRIORITY.get(existing.source_type, 99)
            new_priority = self.SOURCE_PRIORITY.get(provider.source_type, 99)

            if new_priority < existing_priority:
                logger.info(
                    f"Provider '{provider.name}' from {provider.source_type} "
                    f"overrides {existing.source_type} (higher priority)"
                )
                return ConflictResult(
                    resolution=ConflictResolution.SOURCE_PRIORITY,
                    winner=provider,
                    reason=f"{provider.source_type} has higher priority than {existing.source_type}",
                )
            else:
                logger.debug(
                    f"Provider '{provider.name}' from {provider.source_type} "
                    f"rejected (lower priority than {existing.source_type})"
                )
                return ConflictResult(
                    resolution=ConflictResolution.REJECTED,
                    winner=None,
                    reason=f"Existing source {existing.source_type} has higher priority",
                )

        # No conflict - new provider
        logger.info(f"New provider discovered: {provider.name} from {provider.source_type}")
        return ConflictResult(
            resolution=ConflictResolution.REGISTERED,
            winner=provider,
            reason="New provider registered",
        )

    def register(self, provider: DiscoveredProvider) -> None:
        """Mark provider as registered.

        Args:
            provider: Provider to register
        """
        self._registered[provider.name] = provider
        logger.debug(f"Registered provider: {provider.name}")

    def update(self, provider: DiscoveredProvider) -> None:
        """Update registered provider.

        Args:
            provider: Provider with updated configuration
        """
        self._registered[provider.name] = provider
        logger.debug(f"Updated provider: {provider.name}")

    def deregister(self, name: str) -> DiscoveredProvider | None:
        """Remove provider from registry.

        Args:
            name: Provider name to deregister

        Returns:
            The removed provider, or None if not found
        """
        provider = self._registered.pop(name, None)
        if provider:
            logger.info(f"Deregistered provider: {name}")
        return provider

    def get_registered(self, name: str) -> DiscoveredProvider | None:
        """Get a registered provider by name.

        Args:
            name: Provider name

        Returns:
            The registered provider, or None if not found
        """
        return self._registered.get(name)

    def get_all_registered(self) -> dict[str, DiscoveredProvider]:
        """Get all registered providers.

        Returns:
            Dictionary of name -> DiscoveredProvider
        """
        return dict(self._registered)

    def is_registered(self, name: str) -> bool:
        """Check if a provider is registered.

        Args:
            name: Provider name

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
