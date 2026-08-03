# pyright: reportExplicitAny=false

"""Discovery, quarantine and approval of found servers."""

from dataclasses import dataclass

from ..value_objects.compat import (
    accepts_legacy_provider_id,
)
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

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDiscoveryLost(DomainEvent):
    """Published when a previously discovered mcp_server is no longer found."""

    mcp_server_name: str
    source_type: str
    reason: str  # "ttl_expired", "source_removed", etc.

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDiscoveryConfigChanged(DomainEvent):
    """Published when discovered mcp_server configuration changes."""

    mcp_server_name: str
    source_type: str
    old_fingerprint: str
    new_fingerprint: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerQuarantined(DomainEvent):
    """Published when a discovered mcp_server is quarantined."""

    mcp_server_name: str
    source_type: str
    reason: str
    validation_result: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerApproved(DomainEvent):
    """Published when a quarantined mcp_server is approved."""

    mcp_server_name: str
    source_type: str
    approved_by: str  # "manual" or "auto"

    def __post_init__(self):
        super().__init__()


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


@dataclass(init=False)
class ProviderDiscovered(McpServerDiscovered):
    def __init__(
        self,
        provider_name: str = "",
        mcp_server_name: str = "",
        source_type: str = "",
        mode: str = "",
        fingerprint: str = "",
    ):
        provider_name = provider_name or mcp_server_name
        super().__init__(mcp_server_name=provider_name, source_type=source_type, mode=mode, fingerprint=fingerprint)


@dataclass(init=False)
class ProviderDiscoveryLost(McpServerDiscoveryLost):
    def __init__(self, provider_name: str = "", mcp_server_name: str = "", source_type: str = "", reason: str = ""):
        provider_name = provider_name or mcp_server_name
        super().__init__(mcp_server_name=provider_name, source_type=source_type, reason=reason)


@dataclass(init=False)
class ProviderDiscoveryConfigChanged(McpServerDiscoveryConfigChanged):
    def __init__(
        self,
        provider_name: str = "",
        mcp_server_name: str = "",
        source_type: str = "",
        old_fingerprint: str = "",
        new_fingerprint: str = "",
    ):
        provider_name = provider_name or mcp_server_name
        super().__init__(
            mcp_server_name=provider_name,
            source_type=source_type,
            old_fingerprint=old_fingerprint,
            new_fingerprint=new_fingerprint,
        )


@dataclass(init=False)
class ProviderQuarantined(McpServerQuarantined):
    def __init__(
        self,
        provider_name: str = "",
        mcp_server_name: str = "",
        source_type: str = "",
        reason: str = "",
        validation_result: str = "",
    ):
        provider_name = provider_name or mcp_server_name
        super().__init__(
            mcp_server_name=provider_name, source_type=source_type, reason=reason, validation_result=validation_result
        )


@dataclass(init=False)
class ProviderApproved(McpServerApproved):
    def __init__(
        self, provider_name: str = "", mcp_server_name: str = "", source_type: str = "", approved_by: str = ""
    ):
        provider_name = provider_name or mcp_server_name
        super().__init__(mcp_server_name=provider_name, source_type=source_type, approved_by=approved_by)


@dataclass
class DiscoveryCycleCompleted(DomainEvent):
    """Published when a discovery cycle completes."""

    discovered_count: int
    registered_count: int
    deregistered_count: int
    quarantined_count: int
    error_count: int
    duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class DiscoverySourceHealthChanged(DomainEvent):
    """Published when a discovery source health status changes."""

    source_type: str
    is_healthy: bool
    error_message: str | None = None

    def __post_init__(self):
        super().__init__()
