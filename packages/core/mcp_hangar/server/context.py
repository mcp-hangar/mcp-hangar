"""Application context for dependency injection.

Provides a clean way to access application services without global state.
Follows the Dependency Inversion Principle - high-level modules don't depend
on low-level modules, both depend on abstractions.
"""

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..application.discovery import DiscoveryOrchestrator
    from ..application.sagas import GroupRebalanceSaga
    from ..bootstrap.runtime import Runtime
    from ..domain.model import Provider, ProviderGroup
    from ..domain.repository import IProviderRepository


# =============================================================================
# Protocol Interfaces (DIP - Dependency Inversion Principle)
# =============================================================================


@runtime_checkable
class ICommandBus(Protocol):
    """Interface for command bus."""

    def send(self, command: Any) -> Any:
        """Send a command and return result."""
        ...


@runtime_checkable
class IQueryBus(Protocol):
    """Interface for query bus."""

    def execute(self, query: Any) -> Any:
        """Execute a query and return result."""
        ...


@runtime_checkable
class IEventBus(Protocol):
    """Interface for event bus."""

    def publish(self, event: Any) -> None:
        """Publish an event."""
        ...

    def subscribe_to_all(self, handler: Any) -> None:
        """Subscribe to all events."""
        ...


@runtime_checkable
class IRateLimitResult(Protocol):
    """Interface for rate limit check result."""

    @property
    def allowed(self) -> bool:
        """Whether the request is allowed."""
        ...

    @property
    def limit(self) -> int:
        """The rate limit."""
        ...


@runtime_checkable
class IRateLimiter(Protocol):
    """Interface for rate limiter."""

    def consume(self, key: str) -> IRateLimitResult:
        """Check rate limit for a key."""
        ...

    def get_stats(self) -> dict[str, Any]:
        """Get rate limiter statistics."""
        ...


@runtime_checkable
class ISecurityHandler(Protocol):
    """Interface for security handler."""

    def log_rate_limit_exceeded(self, limit: int, window_seconds: int) -> None:
        """Log rate limit exceeded event."""
        ...

    def log_validation_failed(
        self,
        field: str,
        message: str,
        provider_id: Optional[str] = None,
        value: Optional[str] = None,
    ) -> None:
        """Log validation failure."""
        ...

    def handle(self, event: Any) -> None:
        """Handle a security event."""
        ...


@dataclass
class ApplicationContext:
    """Dependency injection container for the application.

    Instead of using global variables, components receive this context
    which contains all dependencies they need. This makes testing easier
    and dependencies explicit.

    Attributes:
        runtime: The application runtime with all infrastructure
        groups: Provider groups for load balancing
        discovery_orchestrator: Optional discovery service
        group_rebalance_saga: Optional saga for group rebalancing
    """

    runtime: "Runtime"
    groups: dict[str, "ProviderGroup"] = field(default_factory=dict)
    discovery_orchestrator: Optional["DiscoveryOrchestrator"] = None
    group_rebalance_saga: Optional["GroupRebalanceSaga"] = None

    @property
    def repository(self) -> "IProviderRepository":
        """Get the provider repository."""
        return self.runtime.repository

    @property
    def command_bus(self) -> ICommandBus:
        """Get the command bus."""
        return self.runtime.command_bus

    @property
    def query_bus(self) -> IQueryBus:
        """Get the query bus."""
        return self.runtime.query_bus

    @property
    def event_bus(self) -> IEventBus:
        """Get the event bus."""
        return self.runtime.event_bus

    @property
    def rate_limiter(self) -> IRateLimiter:
        """Get the rate limiter."""
        return self.runtime.rate_limiter

    @property
    def security_handler(self) -> ISecurityHandler:
        """Get the security handler."""
        return self.runtime.security_handler

    def get_provider(self, provider_id: str) -> Optional["Provider"]:
        """Get a provider by ID."""
        return self.runtime.repository.get(provider_id)

    def provider_exists(self, provider_id: str) -> bool:
        """Check if a provider exists."""
        return self.runtime.repository.exists(provider_id)

    def get_group(self, group_id: str) -> Optional["ProviderGroup"]:
        """Get a group by ID."""
        return self.groups.get(group_id)

    def group_exists(self, group_id: str) -> bool:
        """Check if a group exists."""
        return group_id in self.groups


# Singleton context - initialized lazily or explicitly
_context: Optional[ApplicationContext] = None


def get_context() -> ApplicationContext:
    """Get the application context.

    If context is not initialized, it will be lazily initialized
    with the default runtime. This supports both:
    - Explicit initialization via init_context() for full control
    - Lazy initialization for backward compatibility with tests

    Returns:
        ApplicationContext instance
    """
    global _context
    if _context is None:
        # Lazy initialization with default runtime
        from ..bootstrap.runtime import create_runtime

        runtime = create_runtime()
        _context = ApplicationContext(runtime=runtime)
    return _context


def init_context(runtime: "Runtime") -> ApplicationContext:
    """Initialize the application context.

    Args:
        runtime: The application runtime

    Returns:
        Initialized ApplicationContext
    """
    global _context
    _context = ApplicationContext(runtime=runtime)
    return _context


def reset_context() -> None:
    """Reset context (for testing)."""
    global _context
    _context = None
