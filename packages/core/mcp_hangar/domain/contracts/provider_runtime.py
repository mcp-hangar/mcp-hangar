"""Protocol contracts for provider-like objects.

These contracts define the *minimum* surface area required by infrastructure
components (e.g. background workers and command handlers) without importing
concrete implementations.

Why:
- Avoids duck-typing via hasattr(...)
- Makes the expected interface explicit and type-checkable
- Supports the domain `Provider` aggregate and any compatible implementations

Notes:
- Protocols are for typing only; they don't enforce runtime inheritance.
- Keep these contracts small and stable.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, Protocol, runtime_checkable

from ..events import DomainEvent


@runtime_checkable
class SupportsEventCollection(Protocol):
    """Something that buffers domain events and can expose them for publishing."""

    def collect_events(self) -> Iterable[DomainEvent]:
        """Return all currently buffered domain events and clear the buffer."""
        ...


@runtime_checkable
class SupportsHealthCheck(Protocol):
    """Something that can perform an active health check."""

    def health_check(self) -> bool:
        """Return True if healthy, False otherwise."""
        ...


@runtime_checkable
class SupportsIdleShutdown(Protocol):
    """Something that can shut itself down when idle."""

    def maybe_shutdown_idle(self) -> bool:
        """Shutdown when idle past TTL. Returns True if shutdown happened."""
        ...


@runtime_checkable
class SupportsState(Protocol):
    """Something that exposes a state-like object.

    We intentionally keep this loose: state can be an enum with a `.value`
    or a string. Background worker may normalize this.
    """

    @property
    def state(self) -> Any:  # enum-like or str
        ...


@runtime_checkable
class SupportsHealthStats(Protocol):
    """Something that exposes health stats for metrics."""

    @property
    def health(self) -> Any:
        """Health tracker-like object (must expose `consecutive_failures`)."""
        ...


@runtime_checkable
class SupportsProviderLifecycle(Protocol):
    """Commands-side lifecycle surface required by command handlers."""

    def ensure_ready(self) -> None:
        """Ensure provider is started and ready to accept requests."""
        ...

    def shutdown(self) -> None:
        """Stop provider and release resources."""
        ...


@runtime_checkable
class SupportsToolInvocation(Protocol):
    """Commands-side tool invocation surface required by command handlers."""

    def invoke_tool(self, tool_name: str, arguments: dict[str, Any], timeout: float = 30.0) -> dict[str, Any]:
        """Invoke a tool on the provider."""
        ...

    def get_tool_names(self) -> list[str]:
        """Get list of available tool names."""
        ...


@runtime_checkable
class ProviderRuntime(
    SupportsEventCollection,
    SupportsHealthCheck,
    SupportsIdleShutdown,
    SupportsState,
    SupportsHealthStats,
    SupportsProviderLifecycle,
    SupportsToolInvocation,
    Protocol,
):
    """Provider-like runtime contract required by background worker and command handlers.

    Any object satisfying this protocol can be managed by:
    - GC/health workers
    - CQRS command handlers

    Primary implementation:
    - domain aggregate: `mcp_hangar.domain.model.Provider`
    """

    # No additional members beyond the composed protocols.
    ...


@runtime_checkable
class ProviderMapping(Protocol):
    """Dict-like view of providers consumed by BackgroundWorker.

    BackgroundWorker only needs `.items()` for snapshot iteration.
    """

    def items(self) -> Iterable[tuple[str, ProviderRuntime]]: ...


def normalize_state_to_str(state: Any) -> str:
    """Best-effort normalization of a state-like value to a lower-case string.

    This exists to centralize normalization logic instead of scattering
    `hasattr(state, "value")` checks around infrastructure code.
    """
    if state is None:
        return "unknown"
    value = getattr(state, "value", None)
    if value is not None:
        return str(value).lower()
    return str(state).lower()
