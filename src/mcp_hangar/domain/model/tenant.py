"""Tenant aggregate - multi-tenancy root entity.

Represents a team or business unit with isolated resources, quotas, and usage tracking.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..events import (
    QuotaExceeded,
    QuotaUpdated,
    QuotaWarningThresholdReached,
    TenantCreated,
    TenantReactivated,
    TenantSuspended,
)
from ..value_objects import TenantId
from .aggregate import AggregateRoot


class TenantStatus(Enum):
    """Status of a tenant."""

    ACTIVE = "active"
    SUSPENDED = "suspended"
    PENDING_APPROVAL = "pending_approval"
    ARCHIVED = "archived"


@dataclass
class TenantQuotas:
    """Resource quotas for a tenant.

    Defines limits for various resource types to prevent abuse
    and enable cost control.
    """

    max_providers: int = 50
    max_namespaces: int = 10
    max_tool_invocations_per_hour: int = 10000
    max_tool_invocations_per_day: int = 100000
    max_concurrent_providers: int = 20
    max_cold_starts_per_hour: int = 100
    max_cpu_millicores: int = 8000  # 8 cores
    max_memory_mb: int = 16384  # 16 GB
    max_storage_mb: int = 102400  # 100 GB

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "max_providers": self.max_providers,
            "max_namespaces": self.max_namespaces,
            "max_tool_invocations_per_hour": self.max_tool_invocations_per_hour,
            "max_tool_invocations_per_day": self.max_tool_invocations_per_day,
            "max_concurrent_providers": self.max_concurrent_providers,
            "max_cold_starts_per_hour": self.max_cold_starts_per_hour,
            "max_cpu_millicores": self.max_cpu_millicores,
            "max_memory_mb": self.max_memory_mb,
            "max_storage_mb": self.max_storage_mb,
        }


@dataclass
class TenantUsage:
    """Current resource usage for a tenant.

    Tracks actual usage against quotas for enforcement and reporting.
    """

    provider_count: int = 0
    namespace_count: int = 0
    tool_invocations_last_hour: int = 0
    tool_invocations_today: int = 0
    concurrent_providers: int = 0
    cold_starts_last_hour: int = 0
    cpu_millicores_used: int = 0
    memory_mb_used: int = 0
    storage_mb_used: int = 0

    last_updated: datetime = field(default_factory=lambda: datetime.now())


@dataclass(frozen=True)
class QuotaCheckResult:
    """Result of a quota check operation.

    Attributes:
        allowed: Whether the operation is allowed
        remaining: Remaining quota capacity
        at_warning: Whether usage is at warning threshold (80%)
        current: Current usage level
        limit: Quota limit
    """

    allowed: bool
    remaining: float
    at_warning: bool = False
    current: int = 0
    limit: int = 0

    @property
    def percentage_used(self) -> float:
        """Calculate percentage of quota used."""
        if self.limit == 0:
            return 0.0
        return (self.current / self.limit) * 100


class Tenant(AggregateRoot):
    """Tenant aggregate root.

    Represents a team/business unit with isolated resources.

    Invariants:
    - Tenant ID is immutable after creation
    - Suspended tenants cannot create new resources
    - Usage cannot exceed quotas (enforced at check_quota)
    - Namespaces belong to exactly one tenant
    """

    def __init__(
        self,
        tenant_id: TenantId,
        name: str,
        display_name: str,
        owner_principal_id: str,
        quotas: TenantQuotas | None = None,
        labels: dict[str, str] | None = None,
        annotations: dict[str, str] | None = None,
    ):
        super().__init__()
        self._tenant_id = tenant_id
        self._name = name
        self._display_name = display_name
        self._owner_principal_id = owner_principal_id
        self._status = TenantStatus.ACTIVE
        self._quotas = quotas or TenantQuotas()
        self._usage = TenantUsage()
        self._labels = dict(labels or {})
        self._annotations = dict(annotations or {})
        self._namespaces: set[str] = set()
        self._created_at = datetime.now()
        self._suspended_at: datetime | None = None
        self._suspended_reason: str | None = None

        self._record_event(
            TenantCreated(
                tenant_id=tenant_id.value,
                name=name,
                owner_principal_id=owner_principal_id,
            )
        )

    @property
    def tenant_id(self) -> TenantId:
        """Get tenant identifier."""
        return self._tenant_id

    @property
    def name(self) -> str:
        """Get tenant name."""
        return self._name

    @property
    def status(self) -> TenantStatus:
        """Get tenant status."""
        return self._status

    @property
    def quotas(self) -> TenantQuotas:
        """Get tenant quotas."""
        return self._quotas

    @property
    def usage(self) -> TenantUsage:
        """Get current usage."""
        return self._usage

    @property
    def namespaces(self) -> frozenset[str]:
        """Get namespaces belonging to this tenant."""
        return frozenset(self._namespaces)

    def is_active(self) -> bool:
        """Check if tenant is active."""
        return self._status == TenantStatus.ACTIVE

    def suspend(self, reason: str, suspended_by: str) -> None:
        """Suspend tenant - blocks new resource creation.

        Args:
            reason: Reason for suspension
            suspended_by: Principal who suspended the tenant
        """
        if self._status == TenantStatus.SUSPENDED:
            return

        self._status = TenantStatus.SUSPENDED
        self._suspended_at = datetime.now()
        self._suspended_reason = reason

        self._record_event(
            TenantSuspended(
                tenant_id=self._tenant_id.value,
                reason=reason,
                suspended_by=suspended_by,
            )
        )

    def reactivate(self, reactivated_by: str) -> None:
        """Reactivate a suspended tenant.

        Args:
            reactivated_by: Principal who reactivated the tenant
        """
        if self._status != TenantStatus.SUSPENDED:
            return

        self._status = TenantStatus.ACTIVE
        self._suspended_at = None
        self._suspended_reason = None

        self._record_event(
            TenantReactivated(
                tenant_id=self._tenant_id.value,
                reactivated_by=reactivated_by,
            )
        )

    def update_quotas(self, new_quotas: TenantQuotas, updated_by: str) -> None:
        """Update tenant quotas.

        Args:
            new_quotas: New quota limits
            updated_by: Principal who updated the quotas
        """
        old_quotas = self._quotas
        self._quotas = new_quotas

        self._record_event(
            QuotaUpdated(
                tenant_id=self._tenant_id.value,
                old_quotas=old_quotas.to_dict(),
                new_quotas=new_quotas.to_dict(),
                updated_by=updated_by,
            )
        )

    def check_quota(self, resource_type: str, requested_amount: int = 1) -> QuotaCheckResult:
        """Check if a resource request would exceed quota.

        Args:
            resource_type: Type of resource (providers, tool_invocations_hour, etc.)
            requested_amount: Amount requested

        Returns:
            QuotaCheckResult with allowed status and remaining capacity
        """
        quota_map = {
            "providers": (self._usage.provider_count, self._quotas.max_providers),
            "namespaces": (self._usage.namespace_count, self._quotas.max_namespaces),
            "concurrent_providers": (self._usage.concurrent_providers, self._quotas.max_concurrent_providers),
            "tool_invocations_hour": (
                self._usage.tool_invocations_last_hour,
                self._quotas.max_tool_invocations_per_hour,
            ),
            "tool_invocations_day": (self._usage.tool_invocations_today, self._quotas.max_tool_invocations_per_day),
            "cold_starts_hour": (self._usage.cold_starts_last_hour, self._quotas.max_cold_starts_per_hour),
            "cpu_millicores": (self._usage.cpu_millicores_used, self._quotas.max_cpu_millicores),
            "memory_mb": (self._usage.memory_mb_used, self._quotas.max_memory_mb),
        }

        if resource_type not in quota_map:
            return QuotaCheckResult(allowed=True, remaining=float("inf"))

        current, limit = quota_map[resource_type]
        remaining = limit - current
        would_exceed = (current + requested_amount) > limit

        # Warning threshold at 80%
        warning_threshold = limit * 0.8
        at_warning = current >= warning_threshold and not would_exceed

        if at_warning:
            self._record_event(
                QuotaWarningThresholdReached(
                    tenant_id=self._tenant_id.value,
                    resource_type=resource_type,
                    current_usage=current,
                    limit=limit,
                    percentage=int((current / limit) * 100),
                )
            )

        if would_exceed:
            self._record_event(
                QuotaExceeded(
                    tenant_id=self._tenant_id.value,
                    resource_type=resource_type,
                    requested=requested_amount,
                    current_usage=current,
                    limit=limit,
                )
            )

        return QuotaCheckResult(
            allowed=not would_exceed,
            remaining=remaining,
            at_warning=at_warning,
            current=current,
            limit=limit,
        )

    def record_usage(self, resource_type: str, amount: int) -> None:
        """Record resource usage.

        Args:
            resource_type: Type of resource being used
            amount: Amount to add (can be negative for decrements)
        """
        if resource_type == "tool_invocation":
            self._usage.tool_invocations_last_hour += amount
            self._usage.tool_invocations_today += amount
        elif resource_type == "cold_start":
            self._usage.cold_starts_last_hour += amount
        elif resource_type == "provider_start":
            self._usage.concurrent_providers += amount
        elif resource_type == "provider_stop":
            self._usage.concurrent_providers = max(0, self._usage.concurrent_providers - amount)
        elif resource_type == "provider_create":
            self._usage.provider_count += amount
        elif resource_type == "provider_delete":
            self._usage.provider_count = max(0, self._usage.provider_count - amount)

        self._usage.last_updated = datetime.now()

    def add_namespace(self, namespace: str) -> None:
        """Add a namespace to this tenant.

        Args:
            namespace: Namespace identifier
        """
        if namespace not in self._namespaces:
            self._namespaces.add(namespace)
            self._usage.namespace_count = len(self._namespaces)

    def remove_namespace(self, namespace: str) -> None:
        """Remove a namespace from this tenant.

        Args:
            namespace: Namespace identifier
        """
        self._namespaces.discard(namespace)
        self._usage.namespace_count = len(self._namespaces)
