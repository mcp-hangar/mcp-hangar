# pyright: reportExplicitAny=false

"""Discovery, quarantine and approval of found servers."""

from dataclasses import dataclass

from ..value_objects.compat import accepts_legacy_provider_id, accepts_legacy_provider_name
from .base import DomainEvent
from .health import McpServerIdleDetected
from .lifecycle import McpServerDegraded, McpServerStarted, McpServerStateChanged, McpServerStopped


# Discovery Events


@dataclass
class McpServerDiscovered(DomainEvent):
    """Published when a new mcp_server is discovered."""

    mcp_server_name: str
    source_type: str
    mode: str
    fingerprint: str


@dataclass
class McpServerDiscoveryLost(DomainEvent):
    """Published when a previously discovered mcp_server is no longer found."""

    mcp_server_name: str
    source_type: str
    reason: str  # "ttl_expired", "source_removed", etc.


@dataclass
class McpServerDiscoveryConfigChanged(DomainEvent):
    """Published when discovered mcp_server configuration changes."""

    mcp_server_name: str
    source_type: str
    old_fingerprint: str
    new_fingerprint: str


@dataclass
class McpServerQuarantined(DomainEvent):
    """Published when a discovered mcp_server is quarantined."""

    mcp_server_name: str
    source_type: str
    reason: str
    validation_result: str


@dataclass
class McpServerApproved(DomainEvent):
    """Published when a quarantined mcp_server is approved."""

    mcp_server_name: str
    source_type: str
    approved_by: str  # "manual" or "auto"


@accepts_legacy_provider_id
class ProviderStarted(McpServerStarted):
    """Deprecated alias for :class:`McpServerStarted`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderStopped(McpServerStopped):
    """Deprecated alias for :class:`McpServerStopped`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderDegraded(McpServerDegraded):
    """Deprecated alias for :class:`McpServerDegraded`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderStateChanged(McpServerStateChanged):
    """Deprecated alias for :class:`McpServerStateChanged`, kept for pre-rename callers."""


@accepts_legacy_provider_id
class ProviderIdleDetected(McpServerIdleDetected):
    """Deprecated alias for :class:`McpServerIdleDetected`, kept for pre-rename callers."""


@accepts_legacy_provider_name
class ProviderDiscovered(McpServerDiscovered):
    """Deprecated alias for :class:`McpServerDiscovered`, kept for pre-rename callers."""


@accepts_legacy_provider_name
class ProviderDiscoveryLost(McpServerDiscoveryLost):
    """Deprecated alias for :class:`McpServerDiscoveryLost`, kept for pre-rename callers."""


@accepts_legacy_provider_name
class ProviderDiscoveryConfigChanged(McpServerDiscoveryConfigChanged):
    """Deprecated alias for :class:`McpServerDiscoveryConfigChanged`, kept for pre-rename callers."""


@accepts_legacy_provider_name
class ProviderQuarantined(McpServerQuarantined):
    """Deprecated alias for :class:`McpServerQuarantined`, kept for pre-rename callers."""


@accepts_legacy_provider_name
class ProviderApproved(McpServerApproved):
    """Deprecated alias for :class:`McpServerApproved`, kept for pre-rename callers."""


@dataclass
class DiscoveryCycleCompleted(DomainEvent):
    """Published when a discovery cycle completes."""

    discovered_count: int
    registered_count: int
    deregistered_count: int
    quarantined_count: int
    error_count: int
    duration_ms: float


@dataclass
class DiscoverySourceHealthChanged(DomainEvent):
    """Published when a discovery source health status changes."""

    source_type: str
    is_healthy: bool
    error_message: str | None = None
