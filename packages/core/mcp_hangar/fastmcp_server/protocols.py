"""Protocol definitions for control plane function signatures.

These protocols define the expected signatures for all control plane
functions used by the MCP server factory.
"""

from typing import Any, Protocol

# =============================================================================
# Core Protocols (Required)
# =============================================================================


class HangarListFn(Protocol):
    """Protocol for hangar_list function."""

    def __call__(self, state_filter: str | None = None) -> dict[str, Any]:
        """List all managed providers with lifecycle state and metadata."""
        ...


class HangarStartFn(Protocol):
    """Protocol for hangar_start function."""

    def __call__(self, provider: str) -> dict[str, Any]:
        """Start a provider and discover tools."""
        ...


class HangarStopFn(Protocol):
    """Protocol for hangar_stop function."""

    def __call__(self, provider: str) -> dict[str, Any]:
        """Stop a provider."""
        ...


class HangarInvokeFn(Protocol):
    """Protocol for hangar_invoke function."""

    def __call__(
        self,
        provider: str,
        tool: str,
        arguments: dict[str, Any],
        timeout: float = 30.0,
    ) -> dict[str, Any]:
        """Invoke a tool on a provider."""
        ...


class HangarToolsFn(Protocol):
    """Protocol for hangar_tools function."""

    def __call__(self, provider: str) -> dict[str, Any]:
        """Get tool schemas for a provider."""
        ...


class HangarDetailsFn(Protocol):
    """Protocol for hangar_details function."""

    def __call__(self, provider: str) -> dict[str, Any]:
        """Get detailed provider information."""
        ...


class HangarHealthFn(Protocol):
    """Protocol for hangar_health function."""

    def __call__(self) -> dict[str, Any]:
        """Get control plane health status."""
        ...


# =============================================================================
# Discovery Protocols (Optional)
# =============================================================================


class HangarDiscoverFn(Protocol):
    """Protocol for hangar_discover function (async)."""

    async def __call__(self) -> dict[str, Any]:
        """Trigger immediate discovery cycle."""
        ...


class HangarDiscoveredFn(Protocol):
    """Protocol for hangar_discovered function."""

    def __call__(self) -> dict[str, Any]:
        """List discovered providers pending addition."""
        ...


class HangarQuarantineFn(Protocol):
    """Protocol for hangar_quarantine function."""

    def __call__(self) -> dict[str, Any]:
        """List quarantined providers."""
        ...


class HangarApproveFn(Protocol):
    """Protocol for hangar_approve function (async)."""

    async def __call__(self, provider: str) -> dict[str, Any]:
        """Approve a quarantined provider."""
        ...


class HangarSourcesFn(Protocol):
    """Protocol for hangar_sources function."""

    def __call__(self) -> dict[str, Any]:
        """List discovery sources with status."""
        ...


class HangarMetricsFn(Protocol):
    """Protocol for hangar_metrics function."""

    def __call__(self, format: str = "summary") -> dict[str, Any]:
        """Get control plane metrics."""
        ...


__all__ = [
    # Core protocols
    "HangarListFn",
    "HangarStartFn",
    "HangarStopFn",
    "HangarInvokeFn",
    "HangarToolsFn",
    "HangarDetailsFn",
    "HangarHealthFn",
    # Discovery protocols
    "HangarDiscoverFn",
    "HangarDiscoveredFn",
    "HangarQuarantineFn",
    "HangarApproveFn",
    "HangarSourcesFn",
    "HangarMetricsFn",
]
