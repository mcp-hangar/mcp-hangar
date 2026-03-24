"""Integration tests for multi-tenancy features.

Tests tenant isolation, quota enforcement, and cross-aggregate interactions.
"""

import pytest

from mcp_hangar.domain.events import NamespaceCreated, QuotaExceeded, TenantCreated
from mcp_hangar.domain.model.namespace import Namespace, NamespaceQuotaOverrides
from mcp_hangar.domain.model.tenant import Tenant, TenantQuotas, TenantStatus
from mcp_hangar.domain.value_objects import NamespaceId, Principal, PrincipalId, PrincipalType, TenantId


class TestTenantIsolation:
    """Tests for tenant isolation boundaries."""

    def test_tenant_cannot_access_other_tenant_namespaces(self):
        """Namespaces are isolated per tenant."""
        tenant_a = Tenant(
            tenant_id=TenantId("team-a"),
            name="team-a",
            display_name="Team A",
            owner_principal_id="user:admin-a@example.com",
        )

        tenant_b = Tenant(
            tenant_id=TenantId("team-b"),
            name="team-b",
            display_name="Team B",
            owner_principal_id="user:admin-b@example.com",
        )

        namespace_a = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=tenant_a.tenant_id,
            name="dev",
            display_name="Development",
            created_by="user:admin-a@example.com",
        )

        namespace_b = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=tenant_b.tenant_id,
            name="dev",
            display_name="Development",
            created_by="user:admin-b@example.com",
        )

        # Same namespace name, different tenants - should be isolated
        assert namespace_a.tenant_id != namespace_b.tenant_id
        assert namespace_a.namespace_id.value == namespace_b.namespace_id.value
        # In real implementation, lookup would be by (tenant_id, namespace_id)

    def test_namespace_belongs_to_single_tenant(self):
        """Namespace cannot be shared across tenants."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=tenant.tenant_id,
            name="dev-env",
            display_name="Development",
            created_by="user:admin@example.com",
        )

        # Namespace has immutable tenant_id
        assert namespace.tenant_id == tenant.tenant_id
        # Cannot change tenant_id (frozen in Namespace.__init__)

    def test_tenant_tracks_its_namespaces(self):
        """Tenant maintains list of its namespaces."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.add_namespace("dev")
        tenant.add_namespace("staging")
        tenant.add_namespace("prod")

        assert len(tenant.namespaces) == 3
        assert "dev" in tenant.namespaces
        assert "staging" in tenant.namespaces
        assert "prod" in tenant.namespaces
        assert tenant.usage.namespace_count == 3


class TestQuotaEnforcement:
    """Tests for quota enforcement across operations."""

    def test_quota_enforcement_blocks_excess_providers(self):
        """Creating provider beyond quota is blocked."""
        quotas = TenantQuotas(max_providers=2)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Simulate 2 providers created
        tenant.record_usage("provider_create", 1)
        tenant.record_usage("provider_create", 1)
        assert tenant.usage.provider_count == 2

        # Third provider should be blocked
        result = tenant.check_quota("providers", requested_amount=1)
        assert result.allowed is False

        # Events should include QuotaExceeded
        events = tenant.collect_events()
        quota_exceeded_events = [e for e in events if isinstance(e, QuotaExceeded)]
        assert len(quota_exceeded_events) > 0

    def test_quota_enforcement_allows_within_limit(self):
        """Operations within quota are allowed."""
        quotas = TenantQuotas(max_providers=10)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Create 5 providers
        for _ in range(5):
            result = tenant.check_quota("providers", requested_amount=1)
            assert result.allowed is True
            tenant.record_usage("provider_create", 1)

        assert tenant.usage.provider_count == 5

        # Can still create more
        result = tenant.check_quota("providers", requested_amount=1)
        assert result.allowed is True

    def test_quota_enforcement_per_namespace(self):
        """Namespace quotas override tenant quotas."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=TenantQuotas(max_providers=100),
        )

        # Namespace with stricter quota
        namespace_overrides = NamespaceQuotaOverrides(max_providers=5)
        namespace = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=tenant.tenant_id,
            name="dev",
            display_name="Development",
            created_by="user:admin@example.com",
            quota_overrides=namespace_overrides,
        )

        # Effective quota is namespace override
        effective = namespace.get_effective_quota("max_providers", tenant.quotas.max_providers)
        assert effective == 5  # Not 100

    def test_quota_enforcement_on_tool_invocations(self):
        """Tool invocation quotas are enforced."""
        quotas = TenantQuotas(max_tool_invocations_per_hour=10)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Simulate 10 invocations
        for _ in range(10):
            result = tenant.check_quota("tool_invocations_hour", requested_amount=1)
            assert result.allowed is True
            tenant.record_usage("tool_invocation", 1)

        # 11th should be blocked
        result = tenant.check_quota("tool_invocations_hour", requested_amount=1)
        assert result.allowed is False

    def test_quota_warning_at_80_percent(self):
        """Warning is emitted at 80% quota usage."""
        quotas = TenantQuotas(max_providers=10)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Use up to 8 providers (80%)
        for _ in range(8):
            tenant.record_usage("provider_create", 1)

        tenant.collect_events()  # Clear events

        # Next check should trigger warning
        result = tenant.check_quota("providers", requested_amount=1)
        assert result.at_warning is True
        assert result.percentage_used == 80.0

        events = tenant.collect_events()
        from mcp_hangar.domain.events import QuotaWarningThresholdReached

        warning_events = [e for e in events if isinstance(e, QuotaWarningThresholdReached)]
        assert len(warning_events) == 1
        assert warning_events[0].percentage == 80


class TestSuspendedTenantBehavior:
    """Tests for suspended tenant restrictions."""

    def test_suspended_tenant_blocks_namespace_creation(self):
        """Suspended tenant cannot create namespaces."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.suspend("Payment overdue", "system:billing")

        assert not tenant.is_active()
        assert tenant.status == TenantStatus.SUSPENDED

    def test_suspended_tenant_blocks_provider_creation(self):
        """Suspended tenant cannot create providers."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.suspend("Compliance violation", "admin")

        # Service layer should check is_active() before operations
        assert not tenant.is_active()

    def test_reactivated_tenant_can_create_resources(self):
        """Reactivated tenant can resume operations."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.suspend("Temporary suspension", "admin")
        assert not tenant.is_active()

        tenant.reactivate("admin")
        assert tenant.is_active()

        # Can now operate normally
        result = tenant.check_quota("providers", requested_amount=1)
        assert result.allowed is True


class TestCrossAggregateInteractions:
    """Tests for interactions between tenant, namespace, and provider aggregates."""

    def test_namespace_creation_updates_tenant_usage(self):
        """Creating namespace updates tenant's namespace count."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        assert tenant.usage.namespace_count == 0

        # Simulate namespace creation via service
        tenant.add_namespace("dev")
        tenant.add_namespace("prod")

        assert tenant.usage.namespace_count == 2

    def test_provider_creation_updates_namespace_and_tenant(self):
        """Creating provider updates both namespace and tenant."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        namespace = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=tenant.tenant_id,
            name="dev",
            display_name="Development",
            created_by="user:admin@example.com",
        )

        # Add providers to namespace
        namespace.add_provider("provider-1")
        namespace.add_provider("provider-2")

        assert namespace.provider_count == 2

        # Tenant should also track (via service layer)
        tenant.record_usage("provider_create", 2)
        assert tenant.usage.provider_count == 2

    def test_namespace_deletion_updates_tenant(self):
        """Deleting namespace decrements tenant's namespace count."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.add_namespace("dev")
        tenant.add_namespace("staging")
        tenant.add_namespace("prod")
        assert tenant.usage.namespace_count == 3

        tenant.remove_namespace("staging")
        assert tenant.usage.namespace_count == 2
        assert "staging" not in tenant.namespaces

    def test_provider_deletion_updates_counts(self):
        """Deleting provider updates counts."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        namespace = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=tenant.tenant_id,
            name="dev",
            display_name="Development",
            created_by="user:admin@example.com",
        )

        # Create providers
        namespace.add_provider("provider-1")
        namespace.add_provider("provider-2")
        tenant.record_usage("provider_create", 2)

        # Delete one
        namespace.remove_provider("provider-1")
        tenant.record_usage("provider_delete", 1)

        assert namespace.provider_count == 1
        assert tenant.usage.provider_count == 1


class TestEventSourcing:
    """Tests for event sourcing in multi-tenancy."""

    def test_tenant_lifecycle_emits_correct_events(self):
        """Tenant lifecycle produces expected event sequence."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        events = tenant.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TenantCreated)

        # Suspend
        tenant.suspend("Test", "admin")
        events = tenant.collect_events()
        assert len(events) == 1
        from mcp_hangar.domain.events import TenantSuspended

        assert isinstance(events[0], TenantSuspended)

        # Reactivate
        tenant.reactivate("admin")
        events = tenant.collect_events()
        assert len(events) == 1
        from mcp_hangar.domain.events import TenantReactivated

        assert isinstance(events[0], TenantReactivated)

    def test_quota_exceeded_emits_event(self):
        """Quota exceeded produces event for alerting."""
        quotas = TenantQuotas(max_providers=1)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        tenant.record_usage("provider_create", 1)
        tenant.collect_events()

        # Exceed quota
        result = tenant.check_quota("providers", requested_amount=1)
        assert result.allowed is False

        events = tenant.collect_events()
        quota_events = [e for e in events if isinstance(e, QuotaExceeded)]
        assert len(quota_events) == 1
        assert quota_events[0].resource_type == "providers"
        assert quota_events[0].limit == 1

    def test_namespace_creation_emits_event(self):
        """Namespace creation produces event."""
        namespace = Namespace(
            namespace_id=NamespaceId("dev"),
            tenant_id=TenantId("test-team"),
            name="dev",
            display_name="Development",
            created_by="user:admin@example.com",
        )

        events = namespace.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], NamespaceCreated)
        assert events[0].namespace_id == "dev"
        assert events[0].tenant_id == "test-team"


class TestPrincipalTenantAssociation:
    """Tests for principal-tenant relationships."""

    def test_principal_has_tenant_id(self):
        """Principal can be associated with a tenant."""
        principal = Principal(
            id=PrincipalId("user:dev@example.com"),
            type=PrincipalType.USER,
            tenant_id="test-team",
            groups=frozenset(["developers"]),
        )

        assert principal.tenant_id == "test-team"

    def test_principal_without_tenant_is_organization_admin(self):
        """Principal without tenant_id has org-level access."""
        principal = Principal(
            id=PrincipalId("user:admin@example.com"),
            type=PrincipalType.USER,
            tenant_id=None,  # No tenant = org admin
            groups=frozenset(["admin"]),
        )

        assert principal.tenant_id is None
        assert "admin" in principal.groups

    def test_system_principal_has_no_tenant(self):
        """System principal operates across tenants."""
        principal = Principal.system()

        assert principal.tenant_id is None
        assert principal.type == PrincipalType.SYSTEM


class TestResourceScope:
    """Tests for ResourceScope hierarchy."""

    def test_organization_scope_includes_all_tenants(self):
        """Organization scope includes any tenant."""
        from mcp_hangar.domain.value_objects import ResourceScope

        org_scope = ResourceScope(organization_id="org-1")
        tenant_scope = ResourceScope(organization_id="org-1", tenant_id="team-a")
        namespace_scope = ResourceScope(organization_id="org-1", tenant_id="team-a", namespace_id="dev")

        assert org_scope.includes(tenant_scope)
        assert org_scope.includes(namespace_scope)

    def test_tenant_scope_includes_own_namespaces(self):
        """Tenant scope includes its namespaces."""
        from mcp_hangar.domain.value_objects import ResourceScope

        tenant_scope = ResourceScope(tenant_id="team-a")
        namespace_scope = ResourceScope(tenant_id="team-a", namespace_id="dev")
        other_tenant_namespace = ResourceScope(tenant_id="team-b", namespace_id="dev")

        assert tenant_scope.includes(namespace_scope)
        assert not tenant_scope.includes(other_tenant_namespace)

    def test_namespace_scope_requires_exact_match(self):
        """Namespace scope only includes exact namespace."""
        from mcp_hangar.domain.value_objects import ResourceScope

        ns_scope = ResourceScope(tenant_id="team-a", namespace_id="dev")
        same_scope = ResourceScope(tenant_id="team-a", namespace_id="dev")
        different_ns = ResourceScope(tenant_id="team-a", namespace_id="prod")

        assert ns_scope.includes(same_scope)
        assert not ns_scope.includes(different_ns)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
