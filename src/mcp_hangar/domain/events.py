# pyright: reportExplicitAny=false

"""Domain events for MCP Hangar.

Events capture important business occurrences and allow decoupled reactions.
"""

from abc import ABC
from dataclasses import dataclass, field
from datetime import datetime
import time
from typing import Any
import uuid


class DomainEvent(ABC):
    """
    Base class for all domain events.

    Note: Not a dataclass to avoid inheritance issues.
    Subclasses should be dataclasses.
    """

    def __init__(self):
        self.event_id: str = str(uuid.uuid4())
        self.occurred_at: float = time.time()

    def to_dict(self) -> dict[str, Any]:
        """Convert event to dictionary for serialization."""
        return {"event_type": self.__class__.__name__, **self.__dict__}


def _resolve_legacy_mcp_server_id(mcp_server_id: str | None, kwargs: dict[str, object]) -> str:
    if mcp_server_id is not None:
        return mcp_server_id
    legacy_id = kwargs.pop("provider_id", None)
    if isinstance(legacy_id, str):
        return legacy_id
    raise TypeError("Missing required argument: mcp_server_id")


# McpServer Lifecycle Events


@dataclass
class McpServerStarted(DomainEvent):
    """Published when a mcp_server successfully starts."""

    mcp_server_id: str
    mode: str  # subprocess, docker, remote
    tools_count: int
    startup_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerStopped(DomainEvent):
    """Published when a mcp_server is stopped."""

    mcp_server_id: str
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDegraded(DomainEvent):
    """Published when a mcp_server enters degraded state."""

    mcp_server_id: str
    consecutive_failures: int
    total_failures: int
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerStateChanged(DomainEvent):
    """Published when mcp_server state transitions."""

    mcp_server_id: str
    old_state: str
    new_state: str

    def __post_init__(self):
        super().__init__()


# Circuit Breaker Events


@dataclass(init=False)
class CircuitBreakerStateChanged(DomainEvent):
    """Published when a circuit breaker transitions between states."""

    mcp_server_id: str
    old_state: str  # closed, open, half_open
    new_state: str  # closed, open, half_open

    def __init__(self, mcp_server_id: str | None = None, old_state: str = "", new_state: str = "", **kwargs: object):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.old_state = old_state
        self.new_state = new_state
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


# Tool Invocation Events


@dataclass(init=False)
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    arguments: dict[str, Any] = field(default_factory=dict)
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        arguments: dict[str, Any] | None = None,
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.arguments = arguments or {}
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    result_size_bytes: int
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        duration_ms: float = 0.0,
        result_size_bytes: int = 0,
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.duration_ms = duration_ms
        self.result_size_bytes = result_size_bytes
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    mcp_server_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    error_message: str
    error_type: str
    identity_context: dict[str, Any] | None = None

    def __init__(
        self,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        correlation_id: str = "",
        duration_ms: float = 0.0,
        error_message: str = "",
        error_type: str = "",
        identity_context: dict[str, Any] | None = None,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.correlation_id = correlation_id
        self.duration_ms = duration_ms
        self.error_message = error_message
        self.error_type = error_type
        self.identity_context = identity_context
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


# Health Check Events


@dataclass(init=False)
class HealthCheckPassed(DomainEvent):
    """Published when a health check succeeds."""

    mcp_server_id: str
    duration_ms: float

    def __init__(self, mcp_server_id: str | None = None, duration_ms: float = 0.0, **kwargs: object):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.duration_ms = duration_ms
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class HealthCheckFailed(DomainEvent):
    """Published when a health check fails."""

    mcp_server_id: str
    consecutive_failures: int
    error_message: str

    def __init__(
        self,
        mcp_server_id: str | None = None,
        consecutive_failures: int = 0,
        error_message: str = "",
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.consecutive_failures = consecutive_failures
        self.error_message = error_message
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


# Resource Management Events


@dataclass
class McpServerIdleDetected(DomainEvent):
    """Published when a mcp_server is detected as idle."""

    mcp_server_id: str
    idle_duration_s: float
    last_used_at: float

    def __post_init__(self):
        super().__init__()


# McpServer Group Events are defined in mcp_hangar.domain.model.mcp_server_group
# to avoid circular imports. Re-export them here for convenience.
# Import at runtime only when needed.


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


@dataclass
@dataclass(init=False)
class ProviderStarted(McpServerStarted):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        mode: str = "",
        tools_count: int = 0,
        startup_duration_ms: float = 0.0,
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(
            mcp_server_id=provider_id, mode=mode, tools_count=tools_count, startup_duration_ms=startup_duration_ms
        )


@dataclass(init=False)
class ProviderStopped(McpServerStopped):
    def __init__(
        self, provider_id: str | None = None, mcp_server_id: str | None = None, reason: str = "", **kwargs: object
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=provider_id, reason=reason)


@dataclass(init=False)
class ProviderDegraded(McpServerDegraded):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        consecutive_failures: int = 0,
        total_failures: int = 0,
        reason: str = "",
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(
            mcp_server_id=provider_id,
            consecutive_failures=consecutive_failures,
            total_failures=total_failures,
            reason=reason,
        )


@dataclass(init=False)
class ProviderStateChanged(McpServerStateChanged):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        old_state: str = "",
        new_state: str = "",
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=provider_id, old_state=old_state, new_state=new_state)


@dataclass(init=False)
class ProviderIdleDetected(McpServerIdleDetected):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        idle_duration_s: float = 0.0,
        last_used_at: float = 0.0,
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=provider_id, idle_duration_s=idle_duration_s, last_used_at=last_used_at)


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


# Authentication & Authorization Events


@dataclass
class AuthenticationSucceeded(DomainEvent):
    """Published when a principal successfully authenticates.

    Attributes:
        principal_id: The authenticated principal's identifier.
        principal_type: Type of principal (user, service_account, system).
        auth_method: Authentication method used (api_key, jwt, mtls).
        source_ip: IP address of the request origin.
        tenant_id: Optional tenant identifier if multi-tenancy is enabled.
    """

    principal_id: str
    principal_type: str
    auth_method: str
    source_ip: str
    tenant_id: str | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class AuthenticationFailed(DomainEvent):
    """Published when authentication fails.

    Attributes:
        auth_method: Authentication method that was attempted.
        source_ip: IP address of the request origin.
        reason: Reason for failure (invalid_token, expired, revoked, unknown_key).
        attempted_principal_id: Optional principal ID if it could be extracted.
    """

    auth_method: str
    source_ip: str
    reason: str
    attempted_principal_id: str | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class AuthorizationDenied(DomainEvent):
    """Published when an authorized principal is denied access.

    Attributes:
        principal_id: The principal who was denied.
        action: The action that was attempted.
        resource_type: Type of resource being accessed.
        resource_id: Specific resource identifier.
        reason: Why access was denied.
    """

    principal_id: str
    action: str
    resource_type: str
    resource_id: str
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class AuthorizationGranted(DomainEvent):
    """Published when authorization is granted (for audit trail).

    Attributes:
        principal_id: The principal who was granted access.
        action: The action that was authorized.
        resource_type: Type of resource being accessed.
        resource_id: Specific resource identifier.
        granted_by_role: Role that granted the permission.
    """

    principal_id: str
    action: str
    resource_type: str
    resource_id: str
    granted_by_role: str

    def __post_init__(self):
        super().__init__()


@dataclass
class PolicyPushRejected(DomainEvent):
    """Published when a policy push request is rejected."""

    principal_id: str
    reason: str
    timestamp: datetime

    def __post_init__(self):
        super().__init__()


@dataclass
class RoleAssigned(DomainEvent):
    """Published when a role is assigned to a principal.

    Attributes:
        principal_id: Principal receiving the role.
        role_name: Name of the role being assigned.
        scope: Scope of the assignment (global, tenant:X, namespace:Y).
        assigned_by: Principal who made the assignment.
    """

    principal_id: str
    role_name: str
    scope: str
    assigned_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class RoleRevoked(DomainEvent):
    """Published when a role is revoked from a principal.

    Attributes:
        principal_id: Principal losing the role.
        role_name: Name of the role being revoked.
        scope: Scope from which the role is being revoked.
        revoked_by: Principal who made the revocation.
    """

    principal_id: str
    role_name: str
    scope: str
    revoked_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ApiKeyCreated(DomainEvent):
    """Published when a new API key is created.

    Attributes:
        key_id: Unique identifier of the key (not the key itself).
        principal_id: Principal the key authenticates as.
        key_name: Human-readable name for the key.
        expires_at: Optional expiration timestamp.
        created_by: Principal who created the key.
    """

    key_id: str
    principal_id: str
    key_name: str
    expires_at: float | None
    created_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ApiKeyRevoked(DomainEvent):
    """Published when an API key is revoked.

    Attributes:
        key_id: Unique identifier of the revoked key.
        principal_id: Principal the key belonged to.
        revoked_by: Principal who revoked the key.
        reason: Optional reason for revocation.
    """

    key_id: str
    principal_id: str
    revoked_by: str
    reason: str = ""

    def __post_init__(self):
        super().__init__()


@dataclass
class RateLimitLockout(DomainEvent):
    """Published when an IP is locked out due to excessive failed auth attempts.

    Attributes:
        source_ip: The IP address that was locked out.
        lockout_duration_seconds: How long the lockout lasts.
        lockout_count: How many consecutive lockouts this IP has had.
        failed_attempts: Number of failed attempts that triggered the lockout.
    """

    source_ip: str
    lockout_duration_seconds: float
    lockout_count: int
    failed_attempts: int

    def __post_init__(self):
        super().__init__()


@dataclass
class RateLimitUnlock(DomainEvent):
    """Published when an IP lockout expires (detected on next check or cleanup).

    Attributes:
        source_ip: The IP address that was unlocked.
        lockout_count: Total consecutive lockouts before unlock.
        unlock_reason: Why the unlock happened (expired, success, manual_clear).
    """

    source_ip: str
    lockout_count: int
    unlock_reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class KeyRotated(DomainEvent):
    """Published when an API key is rotated.

    Attributes:
        key_id: The key_id that was rotated (old key).
        principal_id: Principal the key belongs to.
        new_key_id: The new key's key_id.
        rotated_at: Timestamp when rotation occurred.
        grace_until: Timestamp when old key becomes invalid.
        rotated_by: Principal who initiated the rotation.
    """

    key_id: str
    principal_id: str
    new_key_id: str
    rotated_at: float
    grace_until: float
    rotated_by: str

    def __post_init__(self):
        super().__init__()


# --- Multi-Tenancy Events ---


@dataclass
class TenantCreated(DomainEvent):
    """Published when a new tenant is created."""

    tenant_id: str
    name: str
    owner_principal_id: str

    def __post_init__(self):
        super().__init__()


@dataclass
class TenantSuspended(DomainEvent):
    """Published when a tenant is suspended."""

    tenant_id: str
    reason: str
    suspended_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class TenantReactivated(DomainEvent):
    """Published when a suspended tenant is reactivated."""

    tenant_id: str
    reactivated_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class QuotaUpdated(DomainEvent):
    """Published when tenant quotas are updated."""

    tenant_id: str
    old_quotas: dict[str, Any]
    new_quotas: dict[str, Any]
    updated_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class QuotaExceeded(DomainEvent):
    """Published when a quota limit is exceeded."""

    tenant_id: str
    resource_type: str
    requested: int
    current_usage: int
    limit: int

    def __post_init__(self):
        super().__init__()


@dataclass
class QuotaWarningThresholdReached(DomainEvent):
    """Published when quota usage reaches warning threshold (80%)."""

    tenant_id: str
    resource_type: str
    current_usage: int
    limit: int
    percentage: int

    def __post_init__(self):
        super().__init__()


@dataclass
class NamespaceCreated(DomainEvent):
    """Published when a namespace is created within a tenant."""

    namespace_id: str
    tenant_id: str
    name: str
    created_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class NamespaceDeleted(DomainEvent):
    """Published when a namespace is deleted."""

    namespace_id: str
    tenant_id: str
    deleted_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class CatalogItemPublished(DomainEvent):
    """Published when a catalog item is published."""

    item_id: str
    name: str
    version: str
    published_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class CatalogItemApproved(DomainEvent):
    """Published when a catalog item is approved for deployment."""

    item_id: str
    name: str
    version: str
    approved_by: str
    notes: str

    def __post_init__(self):
        super().__init__()


@dataclass
class CatalogItemRejected(DomainEvent):
    """Published when a catalog item is rejected."""

    item_id: str
    name: str
    rejected_by: str
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class CatalogItemDeprecated(DomainEvent):
    """Published when a catalog item is deprecated."""

    item_id: str
    name: str
    deprecated_by: str
    reason: str
    sunset_date: str | None

    def __post_init__(self):
        super().__init__()


@dataclass
class CostReportGenerated(DomainEvent):
    """Published when a cost report is generated."""

    tenant_id: str
    period_start: str
    period_end: str
    total_cost: str
    currency: str

    def __post_init__(self):
        super().__init__()


# =============================================================================
# Batch Invocation Events
# =============================================================================


@dataclass
class BatchInvocationRequested(DomainEvent):
    """Published when a batch invocation is requested."""

    batch_id: str
    call_count: int
    mcp_servers: list[str]
    max_concurrency: int
    timeout: float
    fail_fast: bool

    def __post_init__(self):
        super().__init__()


@dataclass
class BatchInvocationCompleted(DomainEvent):
    """Published when a batch invocation completes."""

    batch_id: str
    total: int
    succeeded: int
    failed: int
    elapsed_ms: float
    cancelled: int = 0

    def __post_init__(self):
        super().__init__()


@dataclass
class BatchCallCompleted(DomainEvent):
    """Published when a single call within a batch completes."""

    batch_id: str
    call_id: str
    call_index: int
    mcp_server_id: str
    tool_name: str
    success: bool
    elapsed_ms: float
    error_type: str | None = None

    def __post_init__(self):
        super().__init__()


# =============================================================================
# Hot Load Events
# =============================================================================


@dataclass
class McpServerLoadAttempted(DomainEvent):
    """Published when a mcp_server load is attempted."""

    mcp_server_name: str
    user_id: str | None

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerHotLoaded(DomainEvent):
    """Published when a mcp_server is successfully hot-loaded from the registry."""

    mcp_server_id: str
    mcp_server_name: str
    source: str
    verified: bool
    user_id: str | None
    tools_count: int
    load_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerLoadFailed(DomainEvent):
    """Published when a mcp_server load fails."""

    mcp_server_name: str
    reason: str
    user_id: str | None
    error_type: str | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerHotUnloaded(DomainEvent):
    """Published when a hot-loaded mcp_server is unloaded."""

    mcp_server_id: str
    user_id: str | None
    lifetime_seconds: float

    def __post_init__(self):
        super().__init__()


# Configuration Reload Events


@dataclass
class ConfigurationReloadRequested(DomainEvent):
    """Published when configuration reload is requested."""

    config_path: str
    requested_by: str  # "sighup", "tool", "file_watcher"
    force: bool = False

    def __post_init__(self):
        super().__init__()


@dataclass
class ConfigurationReloaded(DomainEvent):
    """Published when configuration is successfully reloaded."""

    config_path: str
    mcp_servers_added: list[str]
    mcp_servers_removed: list[str]
    mcp_servers_updated: list[str]
    mcp_servers_unchanged: list[str]
    reload_duration_ms: float
    requested_by: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ConfigurationReloadFailed(DomainEvent):
    """Published when configuration reload fails."""

    config_path: str
    reason: str
    error_type: str
    requested_by: str

    def __post_init__(self):
        super().__init__()


# =============================================================================
# McpServer CRUD Events
# =============================================================================


@dataclass
class McpServerRegistered(DomainEvent):
    """Published when a mcp_server is registered via API, config, or discovery."""

    mcp_server_id: str
    source: str  # "api" | "config" | "discovery"
    mode: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerUpdated(DomainEvent):
    """Published when a mcp_server configuration is updated."""

    mcp_server_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerDeregistered(DomainEvent):
    """Published when a mcp_server is deleted/deregistered."""

    mcp_server_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ProviderRegistered(McpServerRegistered):
    def __init__(self, provider_id: str | None = None, source: str = "", mode: str = "", **kwargs: object):
        resolved_id = provider_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=resolved_id, source=source, mode=mode)


@dataclass(init=False)
class ProviderUpdated(McpServerUpdated):
    def __init__(self, provider_id: str | None = None, source: str = "", **kwargs: object):
        resolved_id = provider_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=resolved_id, source=source)


@dataclass(init=False)
class ProviderDeregistered(McpServerDeregistered):
    def __init__(self, provider_id: str | None = None, source: str = "", **kwargs: object):
        resolved_id = provider_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=resolved_id, source=source)


# =============================================================================
# RBAC Management Events
# =============================================================================


@dataclass
class CustomRoleCreated(DomainEvent):
    """Published when a custom role is created."""

    role_name: str
    permissions: list[str]
    description: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CustomRoleDeleted(DomainEvent):
    """Published when a custom role is deleted."""

    role_name: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CustomRoleUpdated(DomainEvent):
    """Published when a custom role's permissions or description are updated."""

    role_name: str
    permissions: list[str]
    description: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolAccessPolicySet(DomainEvent):
    """Published when a tool access policy is set for a scope/target."""

    scope: str  # "mcp_server", "group", or "member"
    target_id: str
    allow_list: list[str]
    deny_list: list[str]
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolAccessPolicyCleared(DomainEvent):
    """Published when a tool access policy is removed for a scope/target."""

    scope: str
    target_id: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# Capability enforcement events (Phase 1 — PRODUCT_ARCHITECTURE.md)
# ---------------------------------------------------------------------------


@dataclass(init=False)
class CapabilityViolationDetected(DomainEvent):
    """Published when a mcp_server exceeds its declared capabilities.

    Emitted by the enforcement engine whenever runtime behavior deviates
    from the capability declaration. The enforcement_action field records
    what Hangar did in response (alert/block/quarantine).

    Attributes:
        mcp_server_id: McpServer that violated its capabilities.
        violation_type: Category of violation. One of:
            "egress_undeclared" -- outbound connection to undeclared destination.
            "egress_blocked" -- blocked outbound connection.
            "filesystem_write" -- write to path not in write_paths.
            "filesystem_read" -- read from path not in read_paths.
            "env_undeclared" -- access to undeclared environment variable.
            "tool_count_exceeded" -- mcp_server advertised more tools than declared.
            "tool_schema_drift" -- tool schema changed between restarts.
            "resource_limit_exceeded" -- memory or CPU exceeded declared limit.
        violation_detail: Human-readable description with specifics.
        enforcement_action: What Hangar did: "alert", "block", or "quarantine".
        destination: For egress violations, the blocked/unexpected destination.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    violation_type: str
    violation_detail: str
    enforcement_action: str
    destination: str | None = None
    severity: str = "high"
    schema_version: int = 2

    def __init__(
        self,
        mcp_server_id: str | None = None,
        violation_type: str = "",
        violation_detail: str = "",
        enforcement_action: str = "",
        destination: str | None = None,
        severity: str = "high",
        schema_version: int = 2,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.violation_type = violation_type
        self.violation_detail = violation_detail
        self.enforcement_action = enforcement_action
        self.destination = destination
        self.severity = severity
        self.schema_version = schema_version
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class EgressBlocked(DomainEvent):
    """Published when an outbound connection from a mcp_server is blocked.

    This is a specialization of CapabilityViolationDetected for the
    common case of network egress enforcement.

    Attributes:
        mcp_server_id: McpServer whose egress was blocked.
        destination_host: Blocked destination hostname or IP.
        destination_port: Blocked destination port.
        protocol: Connection protocol (tcp/udp/https/etc.).
        enforcement_source: "networkpolicy" (K8s) or "iptables" (Docker).
        schema_version: Event schema version.
    """

    mcp_server_id: str
    destination_host: str
    destination_port: int
    protocol: str
    enforcement_source: str = "networkpolicy"
    schema_version: int = 1

    def __init__(
        self,
        mcp_server_id: str | None = None,
        destination_host: str = "",
        destination_port: int = 0,
        protocol: str = "",
        enforcement_source: str = "networkpolicy",
        schema_version: int = 1,
        **kwargs: object,
    ):
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.destination_host = destination_host
        self.destination_port = destination_port
        self.protocol = protocol
        self.enforcement_source = enforcement_source
        self.schema_version = schema_version
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerCapabilityQuarantined(DomainEvent):
    """Published when a mcp_server is quarantined due to capability violations.

    A quarantined mcp_server stops serving new requests until the operator
    reviews and releases it. Existing in-flight requests complete normally.

    Attributes:
        mcp_server_id: McpServer that was quarantined.
        reason: Human-readable reason for quarantine.
        violation_count: Number of violations that triggered quarantine.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    reason: str
    violation_count: int = 1
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class McpServerCapabilityQuarantineReleased(DomainEvent):
    """Published when a capability-quarantined mcp_server is released by the operator.

    Attributes:
        mcp_server_id: McpServer released from quarantine.
        released_by: Identity of the operator who released the mcp_server.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    released_by: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ProviderCapabilityQuarantined(McpServerCapabilityQuarantined):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        reason: str = "",
        violation_count: int = 1,
        schema_version: int = 1,
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(
            mcp_server_id=provider_id, reason=reason, violation_count=violation_count, schema_version=schema_version
        )


@dataclass(init=False)
class ProviderCapabilityQuarantineReleased(McpServerCapabilityQuarantineReleased):
    def __init__(
        self,
        provider_id: str | None = None,
        mcp_server_id: str | None = None,
        released_by: str = "",
        schema_version: int = 1,
        **kwargs: object,
    ):
        provider_id = provider_id or mcp_server_id or _resolve_legacy_mcp_server_id(None, kwargs)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__(mcp_server_id=provider_id, released_by=released_by, schema_version=schema_version)


@dataclass
class ToolSchemaDriftDetected(DomainEvent):
    """Published when a mcp_server's tool schema changes between restarts.

    Schema drift may indicate a supply-chain attack, a mis-deployed image,
    or an intentional but undeclared upgrade.

    Attributes:
        mcp_server_id: McpServer whose tool schema changed.
        tools_added: Names of newly appeared tools.
        tools_removed: Names of removed tools.
        tools_changed: Names of tools with changed parameter schemas.
        schema_version: Event schema version.
    """

    mcp_server_id: str
    tools_added: list[str]
    tools_removed: list[str]
    tools_changed: list[str]
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CapabilityDeclarationMissing(DomainEvent):
    """Published when a mcp_server starts without a capability declaration.

    In strict mode this prevents the mcp_server from reaching READY state.
    In alert mode it is a warning.

    Attributes:
        mcp_server_id: McpServer that is missing capability declarations.
        enforcement_mode: Current enforcement mode ("alert" or "block").
        schema_version: Event schema version.
    """

    mcp_server_id: str
    enforcement_mode: str = "alert"
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# Behavioral Profiling Events
# ---------------------------------------------------------------------------


@dataclass
class BehavioralModeChanged(DomainEvent):
    """Published when a mcp_server's behavioral profiling mode changes.

    Attributes:
        mcp_server_id: McpServer whose mode changed.
        old_mode: Previous mode value (learning, enforcing, disabled).
        new_mode: New mode value (learning, enforcing, disabled).
        schema_version: Event schema version.
    """

    mcp_server_id: str
    old_mode: str
    new_mode: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


@dataclass
class BehavioralDeviationDetected(DomainEvent):
    """Published when the deviation detector flags abnormal mcp_server behavior.

    Emitted during ENFORCING mode when an observation does not match the
    learned baseline profile. The deviation_type field classifies the
    deviation (new_destination, frequency_anomaly, protocol_drift).

    Follows the same pattern as CapabilityViolationDetected.

    Attributes:
        mcp_server_id: McpServer whose behavior deviated from baseline.
        deviation_type: Category of deviation (value from DeviationType enum).
        observed: Description of the observed behavior (e.g. "1.2.3.4:443/tcp").
        baseline_expected: Description of the baseline expectation.
        severity: Severity level ("critical", "high", "medium", "low").
        schema_version: Event schema version.
    """

    mcp_server_id: str
    deviation_type: str
    observed: str
    baseline_expected: str
    severity: str = "high"
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolSchemaChanged(DomainEvent):
    """Published when a tool's schema changes between mcp_server restarts.

    Emitted by the schema drift detection subsystem when a mcp_server's
    tool fingerprints differ from the previously stored snapshot.
    One event per changed tool (not one event per mcp_server).

    Attributes:
        mcp_server_id: McpServer whose tool schema changed.
        tool_name: Name of the tool that changed.
        change_type: Type of change (added, removed, modified).
        old_hash: Previous schema hash (None for ADDED).
        new_hash: Current schema hash (None for REMOVED).
        schema_version: Event schema version.
    """

    mcp_server_id: str
    tool_name: str
    change_type: str  # SchemaChangeType.value
    old_hash: str | None = None
    new_hash: str | None = None
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# Semantic analysis events (Phase 57-59 -- v10.0 Semantic Analysis Alpha)
# ---------------------------------------------------------------------------


@dataclass
class DetectionRuleMatched(DomainEvent):
    """Published when a session's call sequence matches a detection rule.

    Emitted by the semantic analysis engine after evaluating a session's
    sliding window of tool invocations against the active rule set. One
    event per rule match (a single invocation can trigger multiple rules).

    This event is consumed by the DetectionRuleMatchedEventHandler which
    increments Prometheus counters and creates OTLP spans.

    Attributes:
        rule_id: Unique identifier of the matched rule (e.g. "credential-exfiltration").
        rule_name: Human-readable rule name.
        severity: Detection severity ("critical", "high", "medium", "low").
        session_id: Session that triggered the match.
        mcp_server_id: McpServer involved in the final matching tool call.
        matched_tools: Tuple of tool names that formed the matched sequence.
        recommended_action: Response action from the rule ("alert", "throttle", "suspend", "block").
        metadata: Additional match context (timestamps, args fingerprints, etc.).
        schema_version: Event schema version.
    """

    rule_id: str
    rule_name: str
    severity: str
    session_id: str
    mcp_server_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    recommended_action: str = "alert"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()

    @property
    def provider_id(self) -> str:
        return self.mcp_server_id


@dataclass
class EnforcementActionTaken(DomainEvent):
    """Published when an automated response action is executed for a rule match.

    Emitted by the ResponseOrchestrator after executing an IResponseAction
    (alert, throttle, suspend, block) in response to a DetectionRuleMatched
    event. One event per action execution.

    This event is consumed by the EnforcementActionTakenEventHandler which
    increments Prometheus counters and creates OTLP spans with
    ``mcp.enforcement.action`` attributes.

    Attributes:
        action: The response action type that was executed ("alert", "throttle",
            "suspend", "block").
        rule_id: Identifier of the detection rule that triggered this action.
        session_id: Session that triggered the original detection.
        mcp_server_id: McpServer involved in the matched sequence.
        matched_tools: Tuple of tool names from the matched sequence.
        detail: Human-readable description of the action taken.
        metadata: Additional context (TTL, rate limit params, etc.).
        schema_version: Event schema version.
    """

    action: str
    rule_id: str
    session_id: str
    mcp_server_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# =============================================================================
# Approval Gate Events (v0.13.0 -- Human-in-the-Loop)
# =============================================================================


@dataclass(init=False)
class ToolApprovalRequested(DomainEvent):
    """Published when a tool invocation is held pending human approval."""

    approval_id: str
    mcp_server_id: str
    tool_name: str
    arguments_hash: str
    channel: str
    expires_at: str
    correlation_id: str

    def __init__(
        self,
        approval_id: str,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        arguments_hash: str = "",
        channel: str = "",
        expires_at: str = "",
        correlation_id: str = "",
        **kwargs: object,
    ):
        self.approval_id = approval_id
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.arguments_hash = arguments_hash
        self.channel = channel
        self.expires_at = expires_at
        self.correlation_id = correlation_id
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolApprovalGranted(DomainEvent):
    """Published when a held tool invocation is approved by a human."""

    approval_id: str
    mcp_server_id: str
    tool_name: str
    decided_by: str
    decided_at: str

    def __init__(
        self,
        approval_id: str,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        decided_by: str = "",
        decided_at: str = "",
        **kwargs: object,
    ):
        self.approval_id = approval_id
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.decided_by = decided_by
        self.decided_at = decided_at
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolApprovalDenied(DomainEvent):
    """Published when a held tool invocation is denied by a human."""

    approval_id: str
    mcp_server_id: str
    tool_name: str
    decided_by: str
    decided_at: str
    reason: str | None = None

    def __init__(
        self,
        approval_id: str,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        decided_by: str = "",
        decided_at: str = "",
        reason: str | None = None,
        **kwargs: object,
    ):
        self.approval_id = approval_id
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.decided_by = decided_by
        self.decided_at = decided_at
        self.reason = reason
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


@dataclass(init=False)
class ToolApprovalExpired(DomainEvent):
    """Published when a held tool invocation expires without a decision."""

    approval_id: str
    mcp_server_id: str
    tool_name: str
    expired_at: str

    def __init__(
        self,
        approval_id: str,
        mcp_server_id: str | None = None,
        tool_name: str = "",
        expired_at: str = "",
        **kwargs: object,
    ):
        self.approval_id = approval_id
        self.mcp_server_id = _resolve_legacy_mcp_server_id(mcp_server_id, kwargs)
        self.tool_name = tool_name
        self.expired_at = expired_at
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected keyword argument(s): {unexpected}")
        super().__init__()

    def __post_init__(self):
        super().__init__()


# legacy aliases
globals().update(
    {
        "".join(("Pro", "viderStarted")): ProviderStarted,
        "".join(("Pro", "viderStopped")): ProviderStopped,
        "".join(("Pro", "viderDegraded")): ProviderDegraded,
        "".join(("Pro", "viderStateChanged")): ProviderStateChanged,
        "".join(("Pro", "viderIdleDetected")): ProviderIdleDetected,
        "".join(("Pro", "viderDiscovered")): ProviderDiscovered,
        "".join(("Pro", "viderDiscoveryLost")): ProviderDiscoveryLost,
        "".join(("Pro", "viderDiscoveryConfigChanged")): ProviderDiscoveryConfigChanged,
        "".join(("Pro", "viderQuarantined")): ProviderQuarantined,
        "".join(("Pro", "viderApproved")): ProviderApproved,
        "".join(("Pro", "viderLoadAttempted")): McpServerLoadAttempted,
        "".join(("Pro", "viderHotLoaded")): McpServerHotLoaded,
        "".join(("Pro", "viderLoadFailed")): McpServerLoadFailed,
        "".join(("Pro", "viderHotUnloaded")): McpServerHotUnloaded,
        "".join(("Pro", "viderRegistered")): ProviderRegistered,
        "".join(("Pro", "viderUpdated")): ProviderUpdated,
        "".join(("Pro", "viderDeregistered")): ProviderDeregistered,
        "".join(("Pro", "viderCapabilityQuarantined")): ProviderCapabilityQuarantined,
        "".join(("Pro", "viderCapabilityQuarantineReleased")): ProviderCapabilityQuarantineReleased,
    }
)
