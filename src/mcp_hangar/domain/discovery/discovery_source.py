"""Discovery Source Port (ABC).

Defines the interface for mcp_server discovery sources.
Implementations include Kubernetes, Docker, Filesystem, and Python entrypoints.
"""

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from .discovered_mcp_server import DiscoveredMcpServer


class DiscoveryMode(Enum):
    """How the source handles mcp_server lifecycle.

    ADDITIVE: Only adds new mcp_servers, never removes existing ones.
              Safe for production environments.

    AUTHORITATIVE: Can add AND remove mcp_servers based on what's discovered.
                   Use for dynamic environments like K8s where pods come and go.
    """

    ADDITIVE = "additive"
    AUTHORITATIVE = "authoritative"

    def __str__(self) -> str:
        return self.value


# Type alias for event handlers
EventHandler = Callable[..., Coroutine[Any, Any, None]]


@dataclass(frozen=True)
class SourcePolicyViolation:
    """A source's own reason for refusing something it discovered.

    Exists so a source can express a constraint the core has no vocabulary for.
    Namespace rules used to live in the core's `SecurityConfig` and were applied
    behind `if source_type == "kubernetes"` -- so a security component knew the
    names of sources, and any new source either silently escaped those checks or
    forced its author to edit security code.

    Deliberately a plain reason and a details dict rather than the application
    layer's `ValidationReport`: a source lives on the far side of a port and
    must not have to import upwards to say "no".
    """

    reason: str
    details: dict[str, Any] = field(default_factory=dict)


class DiscoverySource(ABC):
    """Port for mcp_server discovery sources.

    This abstract base class defines the contract for all discovery sources.
    Implementations discover mcp_servers from various infrastructure sources
    and report changes via event hooks.

    Lifecycle:
        1. Source is configured and registered with orchestrator
        2. Orchestrator calls discover() periodically
        3. Source reports new/changed/lost mcp_servers via event hooks
        4. Orchestrator handles registration/deregistration

    Example:
        class MySource(DiscoverySource):
            @property
            def source_type(self) -> str:
                return "my_source"

            async def discover(self) -> List[DiscoveredMcpServer]:
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

        This is the value operators write as `type` in a source's config entry,
        and the one a factory is registered under. Built-ins use `kubernetes`,
        `docker`, `filesystem` and `entrypoint`; a third-party source picks its
        own -- the set is open, and core holds no list of it.

        Returns:
            This source's type, e.g. `kubernetes`.
        """
        ...

    @abstractmethod
    async def discover(self) -> list[DiscoveredMcpServer]:
        """Discover mcp_servers from this source.

        This method is called periodically by the discovery orchestrator.
        It should return all currently available mcp_servers from this source.

        Returns:
            List of discovered mcp_servers

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

    def apply_config(self, config: dict[str, Any]) -> None:
        """Re-apply a source's own configuration to the running instance.

        Called when a source's spec is reconfigured through the registry, so the
        change reaches the live source rather than only the stored spec. The base
        implementation is a no-op: built-in sources bind their configuration
        (socket paths, roots, namespaces) at construction and treat it as
        immutable for the life of the instance, so a config change to one of them
        takes effect when the source is rebuilt, not mid-flight. A source that
        can reconfigure itself in place overrides this.

        Args:
            config: The source's new configuration (the spec's ``config`` block).
        """
        return None

    # Event hooks for observability

    async def on_mcp_server_discovered(self, mcp_server: DiscoveredMcpServer) -> None:
        """Hook called when a new mcp_server is found.

        Args:
            mcp_server: Newly discovered mcp_server
        """
        handler = self._event_handlers.get("discovered")
        if handler:
            await handler(mcp_server)

    async def on_mcp_server_lost(self, mcp_server_name: str) -> None:
        """Hook called when a previously discovered mcp_server disappears.

        Args:
            mcp_server_name: Name of the lost mcp_server
        """
        handler = self._event_handlers.get("lost")
        if handler:
            await handler(mcp_server_name)

    async def on_mcp_server_changed(self, old: DiscoveredMcpServer, new: DiscoveredMcpServer) -> None:
        """Hook called when mcp_server config changes (fingerprint mismatch).

        Args:
            old: Previous mcp_server configuration
            new: New mcp_server configuration
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

    def policy_violation(self, mcp_server: DiscoveredMcpServer) -> "SourcePolicyViolation | None":
        """This source's own reason to refuse something it discovered.

        Optional on purpose. A source that has no constraints of its own says
        nothing, and every existing implementation keeps working unchanged --
        an abstract hook here would break exactly the third-party sources this
        port exists to make cheap.

        Called before the core's own checks (rate, count, health, schema), which
        is where the kubernetes namespace rules used to run from inside a
        `source_type ==` branch. A source now answers for its own world:
        namespaces, projects, datacenters, tenancy -- whatever its vocabulary
        is, the core never learns it.

        Args:
            mcp_server: The discovered server, including its `metadata`, which
                is where a source puts its own concepts.

        Returns:
            A violation to refuse registration, or None to raise no objection.
        """
        return None

    def __str__(self) -> str:
        return f"{self.__class__.__name__}(type={self.source_type}, mode={self.mode})"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(source_type={self.source_type!r}, mode={self.mode!r})"
