"""Domain events for MCP Hangar.

Events capture important business occurrences and allow decoupled reactions.
"""

from abc import ABC
from dataclasses import dataclass, field
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


# Provider Lifecycle Events


@dataclass
class ProviderStarted(DomainEvent):
    """Published when a provider successfully starts."""

    provider_id: str
    mode: str  # subprocess, docker, remote
    tools_count: int
    startup_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderStopped(DomainEvent):
    """Published when a provider stops."""

    provider_id: str
    reason: str  # "shutdown", "idle", "error", "degraded"

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDegraded(DomainEvent):
    """Published when a provider enters degraded state."""

    provider_id: str
    consecutive_failures: int
    total_failures: int
    reason: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderStateChanged(DomainEvent):
    """Published when provider state transitions."""

    provider_id: str
    old_state: str
    new_state: str

    def __post_init__(self):
        super().__init__()


# Tool Invocation Events


@dataclass
class ToolInvocationRequested(DomainEvent):
    """Published when a tool invocation is requested."""

    provider_id: str
    tool_name: str
    correlation_id: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    provider_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    result_size_bytes: int = 0

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    provider_id: str
    tool_name: str
    correlation_id: str
    error_message: str
    error_type: str

    def __post_init__(self):
        super().__init__()


# Health Check Events


@dataclass
class HealthCheckPassed(DomainEvent):
    """Published when a health check succeeds."""

    provider_id: str
    duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class HealthCheckFailed(DomainEvent):
    """Published when a health check fails."""

    provider_id: str
    consecutive_failures: int
    error_message: str

    def __post_init__(self):
        super().__init__()


# Resource Management Events


@dataclass
class ProviderIdleDetected(DomainEvent):
    """Published when a provider is detected as idle."""

    provider_id: str
    idle_duration_s: float
    last_used_at: float

    def __post_init__(self):
        super().__init__()


# Provider Group Events are defined in mcp_hangar.domain.model.provider_group
# to avoid circular imports. Re-export them here for convenience.
# Import at runtime only when needed.


# Discovery Events


@dataclass
class ProviderDiscovered(DomainEvent):
    """Published when a new provider is discovered."""

    provider_name: str
    source_type: str
    mode: str
    fingerprint: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDiscoveryLost(DomainEvent):
    """Published when a previously discovered provider is no longer found."""

    provider_name: str
    source_type: str
    reason: str  # "ttl_expired", "source_removed", etc.

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDiscoveryConfigChanged(DomainEvent):
    """Published when discovered provider configuration changes."""

    provider_name: str
    source_type: str
    old_fingerprint: str
    new_fingerprint: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderQuarantined(DomainEvent):
    """Published when a discovered provider is quarantined."""

    provider_name: str
    source_type: str
    reason: str
    validation_result: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderApproved(DomainEvent):
    """Published when a quarantined provider is approved."""

    provider_name: str
    source_type: str
    approved_by: str  # "manual" or "auto"

    def __post_init__(self):
        super().__init__()


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
    old_quotas: dict
    new_quotas: dict
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
    providers: list[str]
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
    provider_id: str
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
class ProviderLoadAttempted(DomainEvent):
    """Published when a provider load is attempted."""

    provider_name: str
    user_id: str | None

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderHotLoaded(DomainEvent):
    """Published when a provider is successfully hot-loaded from the registry."""

    provider_id: str
    provider_name: str
    source: str
    verified: bool
    user_id: str | None
    tools_count: int
    load_duration_ms: float

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderLoadFailed(DomainEvent):
    """Published when a provider load fails."""

    provider_name: str
    reason: str
    user_id: str | None
    error_type: str | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderHotUnloaded(DomainEvent):
    """Published when a hot-loaded provider is unloaded."""

    provider_id: str
    user_id: str | None
    lifetime_seconds: float

    def __post_init__(self):
        super().__init__()
