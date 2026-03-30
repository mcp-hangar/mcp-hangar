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
    """Published when a provider is stopped."""

    provider_id: str
    reason: str

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


# Circuit Breaker Events


@dataclass
class CircuitBreakerStateChanged(DomainEvent):
    """Published when a circuit breaker transitions between states."""

    provider_id: str
    old_state: str  # closed, open, half_open
    new_state: str  # closed, open, half_open

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
    identity_context: dict[str, Any] | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationCompleted(DomainEvent):
    """Published when a tool invocation completes successfully."""

    provider_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    result_size_bytes: int
    identity_context: dict[str, Any] | None = None

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolInvocationFailed(DomainEvent):
    """Published when a tool invocation fails."""

    provider_id: str
    tool_name: str
    correlation_id: str
    duration_ms: float
    error_message: str
    error_type: str
    identity_context: dict[str, Any] | None = None

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
    providers_added: list[str]
    providers_removed: list[str]
    providers_updated: list[str]
    providers_unchanged: list[str]
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
# Provider CRUD Events
# =============================================================================


@dataclass
class ProviderRegistered(DomainEvent):
    """Published when a provider is registered via API, config, or discovery."""

    provider_id: str
    source: str  # "api" | "config" | "discovery"
    mode: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderUpdated(DomainEvent):
    """Published when a provider configuration is updated."""

    provider_id: str
    source: str

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderDeregistered(DomainEvent):
    """Published when a provider is deleted/deregistered."""

    provider_id: str
    source: str

    def __post_init__(self):
        super().__init__()


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

    scope: str  # "provider", "group", or "member"
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


@dataclass
class CapabilityViolationDetected(DomainEvent):
    """Published when a provider exceeds its declared capabilities.

    Emitted by the enforcement engine whenever runtime behavior deviates
    from the capability declaration. The enforcement_action field records
    what Hangar did in response (alert/block/quarantine).

    Attributes:
        provider_id: Provider that violated its capabilities.
        violation_type: Category of violation. One of:
            "egress_undeclared" -- outbound connection to undeclared destination.
            "egress_blocked" -- blocked outbound connection.
            "filesystem_write" -- write to path not in write_paths.
            "filesystem_read" -- read from path not in read_paths.
            "env_undeclared" -- access to undeclared environment variable.
            "tool_count_exceeded" -- provider advertised more tools than declared.
            "tool_schema_drift" -- tool schema changed between restarts.
            "resource_limit_exceeded" -- memory or CPU exceeded declared limit.
        violation_detail: Human-readable description with specifics.
        enforcement_action: What Hangar did: "alert", "block", or "quarantine".
        destination: For egress violations, the blocked/unexpected destination.
        schema_version: Event schema version.
    """

    provider_id: str
    violation_type: str
    violation_detail: str
    enforcement_action: str
    destination: str | None = None
    severity: str = "high"
    schema_version: int = 2

    def __post_init__(self):
        super().__init__()


@dataclass
class EgressBlocked(DomainEvent):
    """Published when an outbound connection from a provider is blocked.

    This is a specialization of CapabilityViolationDetected for the
    common case of network egress enforcement.

    Attributes:
        provider_id: Provider whose egress was blocked.
        destination_host: Blocked destination hostname or IP.
        destination_port: Blocked destination port.
        protocol: Connection protocol (tcp/udp/https/etc.).
        enforcement_source: "networkpolicy" (K8s) or "iptables" (Docker).
        schema_version: Event schema version.
    """

    provider_id: str
    destination_host: str
    destination_port: int
    protocol: str
    enforcement_source: str = "networkpolicy"
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderCapabilityQuarantined(DomainEvent):
    """Published when a provider is quarantined due to capability violations.

    A quarantined provider stops serving new requests until the operator
    reviews and releases it. Existing in-flight requests complete normally.

    Attributes:
        provider_id: Provider that was quarantined.
        reason: Human-readable reason for quarantine.
        violation_count: Number of violations that triggered quarantine.
        schema_version: Event schema version.
    """

    provider_id: str
    reason: str
    violation_count: int = 1
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ProviderCapabilityQuarantineReleased(DomainEvent):
    """Published when a capability-quarantined provider is released by the operator.

    Attributes:
        provider_id: Provider released from quarantine.
        released_by: Identity of the operator who released the provider.
        schema_version: Event schema version.
    """

    provider_id: str
    released_by: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolSchemaDriftDetected(DomainEvent):
    """Published when a provider's tool schema changes between restarts.

    Schema drift may indicate a supply-chain attack, a mis-deployed image,
    or an intentional but undeclared upgrade.

    Attributes:
        provider_id: Provider whose tool schema changed.
        tools_added: Names of newly appeared tools.
        tools_removed: Names of removed tools.
        tools_changed: Names of tools with changed parameter schemas.
        schema_version: Event schema version.
    """

    provider_id: str
    tools_added: list[str]
    tools_removed: list[str]
    tools_changed: list[str]
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class CapabilityDeclarationMissing(DomainEvent):
    """Published when a provider starts without a capability declaration.

    In strict mode this prevents the provider from reaching READY state.
    In alert mode it is a warning.

    Attributes:
        provider_id: Provider that is missing capability declarations.
        enforcement_mode: Current enforcement mode ("alert" or "block").
        schema_version: Event schema version.
    """

    provider_id: str
    enforcement_mode: str = "alert"
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


# ---------------------------------------------------------------------------
# Behavioral Profiling Events
# ---------------------------------------------------------------------------


@dataclass
class BehavioralModeChanged(DomainEvent):
    """Published when a provider's behavioral profiling mode changes.

    Attributes:
        provider_id: Provider whose mode changed.
        old_mode: Previous mode value (learning, enforcing, disabled).
        new_mode: New mode value (learning, enforcing, disabled).
        schema_version: Event schema version.
    """

    provider_id: str
    old_mode: str
    new_mode: str
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class BehavioralDeviationDetected(DomainEvent):
    """Published when the deviation detector flags abnormal provider behavior.

    Emitted during ENFORCING mode when an observation does not match the
    learned baseline profile. The deviation_type field classifies the
    deviation (new_destination, frequency_anomaly, protocol_drift).

    Follows the same pattern as CapabilityViolationDetected.

    Attributes:
        provider_id: Provider whose behavior deviated from baseline.
        deviation_type: Category of deviation (value from DeviationType enum).
        observed: Description of the observed behavior (e.g. "1.2.3.4:443/tcp").
        baseline_expected: Description of the baseline expectation.
        severity: Severity level ("critical", "high", "medium", "low").
        schema_version: Event schema version.
    """

    provider_id: str
    deviation_type: str
    observed: str
    baseline_expected: str
    severity: str = "high"
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


@dataclass
class ToolSchemaChanged(DomainEvent):
    """Published when a tool's schema changes between provider restarts.

    Emitted by the schema drift detection subsystem when a provider's
    tool fingerprints differ from the previously stored snapshot.
    One event per changed tool (not one event per provider).

    Attributes:
        provider_id: Provider whose tool schema changed.
        tool_name: Name of the tool that changed.
        change_type: Type of change (added, removed, modified).
        old_hash: Previous schema hash (None for ADDED).
        new_hash: Current schema hash (None for REMOVED).
        schema_version: Event schema version.
    """

    provider_id: str
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
        provider_id: Provider involved in the final matching tool call.
        matched_tools: Tuple of tool names that formed the matched sequence.
        recommended_action: Response action from the rule ("alert", "throttle", "suspend", "block").
        metadata: Additional match context (timestamps, args fingerprints, etc.).
        schema_version: Event schema version.
    """

    rule_id: str
    rule_name: str
    severity: str
    session_id: str
    provider_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    recommended_action: str = "alert"
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()


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
        provider_id: Provider involved in the matched sequence.
        matched_tools: Tuple of tool names from the matched sequence.
        detail: Human-readable description of the action taken.
        metadata: Additional context (TTL, rate limit params, etc.).
        schema_version: Event schema version.
    """

    action: str
    rule_id: str
    session_id: str
    provider_id: str
    matched_tools: tuple[str, ...] = field(default_factory=tuple)
    detail: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: int = 1

    def __post_init__(self):
        super().__init__()
