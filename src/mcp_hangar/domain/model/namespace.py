"""Namespace aggregate - workload isolation within a tenant.

Represents an environment or project boundary within a tenant.
Maps to Kubernetes namespaces when using K8s operator.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from ..events import NamespaceCreated, NamespaceDeleted
from ..value_objects import NamespaceId, TenantId
from .aggregate import AggregateRoot


class NamespaceStatus(Enum):
    """Status of a namespace."""

    ACTIVE = "active"
    TERMINATING = "terminating"


@dataclass
class NamespaceQuotaOverrides:
    """Per-namespace quota overrides.

    Overrides must be <= tenant quotas. Used to restrict namespace
    usage below tenant-level quotas.
    """

    max_mcp_servers: int | None = None
    max_concurrent_mcp_servers: int | None = None
    max_tool_invocations_per_hour: int | None = None
    max_cpu_millicores: int | None = None
    max_memory_mb: int | None = None


class Namespace(AggregateRoot):
    """Namespace aggregate root.

    Provides isolation boundary within a tenant for workload segregation.

    Invariants:
    - Namespace belongs to exactly one tenant
    - Namespace quota overrides cannot exceed tenant quotas
    - McpServer identifiers are unique within namespace
    """

    def __init__(
        self,
        namespace_id: NamespaceId,
        tenant_id: TenantId,
        name: str,
        display_name: str,
        created_by: str,
        quota_overrides: NamespaceQuotaOverrides | None = None,
        labels: dict[str, str] | None = None,
    ):
        super().__init__()
        self._namespace_id = namespace_id
        self._tenant_id = tenant_id
        self._name = name
        self._display_name = display_name
        self._created_by = created_by
        self._status = NamespaceStatus.ACTIVE
        self._quota_overrides = quota_overrides or NamespaceQuotaOverrides()
        self._labels = dict(labels or {})
        self._mcp_servers: set[str] = set()
        self._created_at = datetime.now()

        self._record_event(
            NamespaceCreated(
                namespace_id=namespace_id.value,
                tenant_id=tenant_id.value,
                name=name,
                created_by=created_by,
            )
        )

    @property
    def namespace_id(self) -> NamespaceId:
        """Get namespace identifier."""
        return self._namespace_id

    @property
    def tenant_id(self) -> TenantId:
        """Get parent tenant identifier."""
        return self._tenant_id

    @property
    def name(self) -> str:
        """Get namespace name."""
        return self._name

    @property
    def mcp_servers(self) -> frozenset[str]:
        """Get mcp_server identifiers in this namespace."""
        return frozenset(self._mcp_servers)

    @property
    def mcp_server_count(self) -> int:
        """Get count of mcp_servers in this namespace."""
        return len(self._mcp_servers)

    def add_mcp_server(self, mcp_server_id: str) -> None:
        """Add a mcp_server to this namespace.

        Args:
            mcp_server_id: McpServer identifier
        """
        self._mcp_servers.add(mcp_server_id)

    def add_provider(self, provider_id: str) -> None:
        """Legacy alias for add_mcp_server."""
        self.add_mcp_server(provider_id)

    def remove_mcp_server(self, mcp_server_id: str) -> None:
        """Remove a mcp_server from this namespace.

        Args:
            mcp_server_id: McpServer identifier
        """
        self._mcp_servers.discard(mcp_server_id)

    def remove_provider(self, provider_id: str) -> None:
        """Legacy alias for remove_mcp_server."""
        self.remove_mcp_server(provider_id)

    def get_effective_quota(self, quota_type: str, tenant_quota: int) -> int:
        """Get effective quota considering overrides.

        Namespace overrides are capped at tenant quota level.

        Args:
            quota_type: Type of quota (max_mcp_servers, max_concurrent_mcp_servers, etc.)
            tenant_quota: Tenant-level quota for this resource type

        Returns:
            Effective quota (min of override and tenant quota)
        """
        override_map = {
            "max_mcp_servers": self._quota_overrides.max_mcp_servers,
            "max_concurrent_mcp_servers": self._quota_overrides.max_concurrent_mcp_servers,
            "max_tool_invocations_per_hour": self._quota_overrides.max_tool_invocations_per_hour,
            "max_cpu_millicores": self._quota_overrides.max_cpu_millicores,
            "max_memory_mb": self._quota_overrides.max_memory_mb,
        }

        override = override_map.get(quota_type)
        if override is not None:
            # Override cannot exceed tenant quota
            return min(override, tenant_quota)
        return tenant_quota

    def mark_terminating(self, deleted_by: str) -> None:
        """Mark namespace as terminating.

        Args:
            deleted_by: Principal who initiated deletion
        """
        self._status = NamespaceStatus.TERMINATING

        self._record_event(
            NamespaceDeleted(
                namespace_id=self._namespace_id.value,
                tenant_id=self._tenant_id.value,
                deleted_by=deleted_by,
            )
        )
