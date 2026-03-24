"""Discovery Source Port (ABC).

Defines the interface for provider discovery sources.
Implementations include Kubernetes, Docker, Filesystem, and Python entrypoints.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from enum import Enum
from typing import Any

from .discovered_provider import DiscoveredProvider


class DiscoveryMode(Enum):
    """How the source handles provider lifecycle.

    ADDITIVE: Only adds new providers, never removes existing ones.
              Safe for production environments.

    AUTHORITATIVE: Can add AND remove providers based on what's discovered.
                   Use for dynamic environments like K8s where pods come and go.
    """

    ADDITIVE = "additive"
    AUTHORITATIVE = "authoritative"

    def __str__(self) -> str:
        return self.value


# Type alias for event handlers
EventHandler = Callable[..., Coroutine[Any, Any, None]]


class DiscoverySource(ABC):
    """Port for provider discovery sources.

    This abstract base class defines the contract for all discovery sources.
    Implementations discover providers from various infrastructure sources
    and report changes via event hooks.

    Lifecycle:
        1. Source is configured and registered with orchestrator
        2. Orchestrator calls discover() periodically
        3. Source reports new/changed/lost providers via event hooks
        4. Orchestrator handles registration/deregistration

    Example:
        class MySource(DiscoverySource):
            @property
            def source_type(self) -> str:
                return "my_source"

            async def discover(self) -> List[DiscoveredProvider]:
                # Implementation
                pass

            async def health_check(self) -> bool:
                return True
    """

    def __init__(self, mode: DiscoveryMode = DiscoveryMode.ADDITIVE):
        """Initialize discovery source.

        Args:
            mode: Discovery mode (additive or authoritative)
        """
        self.mode = mode
        self._event_handlers: dict[str, EventHandler] = {}
        self._enabled = True

    @property
    @abstractmethod
    def source_type(self) -> str:
        """Return source identifier.

        Returns:
            One of: kubernetes, docker, filesystem, entrypoint
        """
        ...

    @abstractmethod
    async def discover(self) -> list[DiscoveredProvider]:
        """Discover providers from this source.

        This method is called periodically by the discovery orchestrator.
        It should return all currently available providers from this source.

        Returns:
            List of discovered providers

        Raises:
            Exception: If discovery fails (will be logged and retried)
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """Check if source is available and healthy.

        Returns:
            True if source can perform discovery, False otherwise
        """
        ...

    @property
    def is_enabled(self) -> bool:
        """Check if source is enabled."""
        return self._enabled

    def enable(self) -> None:
        """Enable this discovery source."""
        self._enabled = True

    def disable(self) -> None:
        """Disable this discovery source."""
        self._enabled = False

    # Event hooks for observability

    async def on_provider_discovered(self, provider: DiscoveredProvider) -> None:
        """Hook called when a new provider is found.

        Args:
            provider: Newly discovered provider
        """
        handler = self._event_handlers.get("discovered")
        if handler:
            await handler(provider)

    async def on_provider_lost(self, provider_name: str) -> None:
        """Hook called when a previously discovered provider disappears.

        Args:
            provider_name: Name of the lost provider
        """
        handler = self._event_handlers.get("lost")
        if handler:
            await handler(provider_name)

    async def on_provider_changed(self, old: DiscoveredProvider, new: DiscoveredProvider) -> None:
        """Hook called when provider config changes (fingerprint mismatch).

        Args:
            old: Previous provider configuration
            new: New provider configuration
        """
        handler = self._event_handlers.get("changed")
        if handler:
            await handler(old, new)

    def register_handler(self, event: str, handler: EventHandler) -> None:
        """Register event handler.

        Args:
            event: Event name (discovered, lost, changed)
            handler: Async callback function
        """
        self._event_handlers[event] = handler

    def unregister_handler(self, event: str) -> EventHandler | None:
        """Unregister event handler.

        Args:
            event: Event name to unregister

        Returns:
            The removed handler, or None if not found
        """
        return self._event_handlers.pop(event, None)

    async def start(self) -> None:
        """Start the discovery source (optional lifecycle hook).

        Override this method to perform initialization tasks like
        starting file watchers or establishing connections.
        """
        pass

    async def stop(self) -> None:
        """Stop the discovery source (optional lifecycle hook).

        Override this method to perform cleanup tasks like
        stopping file watchers or closing connections.
        """
        pass

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(type={self.source_type}, mode={self.mode})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source_type={self.source_type!r}, mode={self.mode!r})"
