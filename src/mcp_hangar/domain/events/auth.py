# pyright: reportExplicitAny=false

"""Authentication, authorization, tenancy and quota events."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .base import DomainEvent


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
