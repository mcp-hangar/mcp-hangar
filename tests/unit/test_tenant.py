"""Unit tests for Tenant and Namespace aggregates."""

import pytest

from mcp_hangar.domain.events import (
    NamespaceCreated,
    QuotaExceeded,
    QuotaUpdated,
    QuotaWarningThresholdReached,
    TenantCreated,
    TenantReactivated,
    TenantSuspended,
)
from mcp_hangar.domain.model.namespace import Namespace, NamespaceQuotaOverrides
from mcp_hangar.domain.model.tenant import Tenant, TenantQuotas, TenantStatus
from mcp_hangar.domain.value_objects import NamespaceId, TenantId


class TestTenant:
    """Tests for Tenant aggregate."""

    def test_create_tenant_with_default_quotas(self):
        """New tenant gets default quotas."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        assert tenant.tenant_id.value == "test-team"
        assert tenant.status == TenantStatus.ACTIVE
        assert tenant.quotas.max_providers == 50
        assert tenant.quotas.max_namespaces == 10

        events = tenant.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TenantCreated)
        assert events[0].tenant_id == "test-team"

    def test_create_tenant_with_custom_quotas(self):
        """Tenant can be created with custom quotas."""
        quotas = TenantQuotas(
            max_providers=100,
            max_namespaces=20,
            max_tool_invocations_per_hour=20000,
        )

        tenant = Tenant(
            tenant_id=TenantId("premium-team"),
            name="premium-team",
            display_name="Premium Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        assert tenant.quotas.max_providers == 100
        assert tenant.quotas.max_namespaces == 20
        assert tenant.quotas.max_tool_invocations_per_hour == 20000

    def test_suspend_tenant_blocks_new_resources(self):
        """Suspended tenant status is updated."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )
        tenant.collect_events()  # Clear creation events

        tenant.suspend("Payment overdue", "system:billing")

        assert tenant.status == TenantStatus.SUSPENDED
        assert not tenant.is_active()

        events = tenant.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TenantSuspended)
        assert events[0].reason == "Payment overdue"
        assert events[0].suspended_by == "system:billing"

    def test_reactivate_suspended_tenant(self):
        """Suspended tenant can be reactivated."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )
        tenant.suspend("Test suspension", "admin")
        tenant.collect_events()

        tenant.reactivate("admin")

        assert tenant.status == TenantStatus.ACTIVE
        assert tenant.is_active()

        events = tenant.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], TenantReactivated)

    def test_quota_check_allows_within_limit(self):
        """Quota check allows operations within limit."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        result = tenant.check_quota("providers", requested_amount=1)

        assert result.allowed is True
        assert result.remaining == 50  # Default max_providers

    def test_quota_check_blocks_when_exceeded(self):
        """Exceeding quota returns not allowed."""
        quotas = TenantQuotas(max_providers=5)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Simulate usage up to limit
        tenant._usage.provider_count = 5
        tenant.collect_events()

        result = tenant.check_quota("providers", requested_amount=1)

        assert result.allowed is False
        assert result.current == 5
        assert result.limit == 5

        events = tenant.collect_events()
        assert any(isinstance(e, QuotaExceeded) for e in events)

    def test_quota_check_warns_at_threshold(self):
        """80% usage triggers warning event."""
        quotas = TenantQuotas(max_providers=10)
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
            quotas=quotas,
        )

        # Simulate usage at 80%
        tenant._usage.provider_count = 8
        tenant.collect_events()

        result = tenant.check_quota("providers", requested_amount=1)

        assert result.allowed is True  # Still within limit
        assert result.at_warning is True

        events = tenant.collect_events()
        assert any(isinstance(e, QuotaWarningThresholdReached) for e in events)
        warning_event = next(e for e in events if isinstance(e, QuotaWarningThresholdReached))
        assert warning_event.percentage == 80

    def test_usage_recording_updates_counters(self):
        """Recording usage increments correct counters."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.record_usage("tool_invocation", 10)
        assert tenant.usage.tool_invocations_last_hour == 10
        assert tenant.usage.tool_invocations_today == 10

        tenant.record_usage("cold_start", 2)
        assert tenant.usage.cold_starts_last_hour == 2

        tenant.record_usage("provider_start", 3)
        assert tenant.usage.concurrent_providers == 3

    def test_add_namespace_updates_count(self):
        """Adding namespace updates usage counter."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )

        tenant.add_namespace("dev")
        tenant.add_namespace("prod")

        assert len(tenant.namespaces) == 2
        assert tenant.usage.namespace_count == 2
        assert "dev" in tenant.namespaces
        assert "prod" in tenant.namespaces

    def test_remove_namespace_updates_count(self):
        """Removing namespace updates usage counter."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )
        tenant.add_namespace("dev")
        tenant.add_namespace("prod")

        tenant.remove_namespace("dev")

        assert len(tenant.namespaces) == 1
        assert tenant.usage.namespace_count == 1
        assert "dev" not in tenant.namespaces

    def test_update_quotas_emits_event(self):
        """Updating quotas emits QuotaUpdated event."""
        tenant = Tenant(
            tenant_id=TenantId("test-team"),
            name="test-team",
            display_name="Test Team",
            owner_principal_id="user:admin@example.com",
        )
        tenant.collect_events()

        new_quotas = TenantQuotas(max_providers=100)
        tenant.update_quotas(new_quotas, "admin")

        events = tenant.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], QuotaUpdated)
        assert events[0].new_quotas["max_providers"] == 100


class TestNamespace:
    """Tests for Namespace aggregate."""

    def test_create_namespace(self):
        """Namespace is created with proper initialization."""
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
        )

        assert namespace.namespace_id.value == "dev-env"
        assert namespace.tenant_id.value == "test-team"
        assert namespace.provider_count == 0

        events = namespace.collect_events()
        assert len(events) == 1
        assert isinstance(events[0], NamespaceCreated)

    def test_add_provider_updates_set(self):
        """Adding provider updates provider set."""
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
        )

        namespace.add_provider("provider-1")
        namespace.add_provider("provider-2")

        assert namespace.provider_count == 2
        assert "provider-1" in namespace.providers
        assert "provider-2" in namespace.providers

    def test_remove_provider_updates_set(self):
        """Removing provider updates provider set."""
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
        )
        namespace.add_provider("provider-1")
        namespace.add_provider("provider-2")

        namespace.remove_provider("provider-1")

        assert namespace.provider_count == 1
        assert "provider-1" not in namespace.providers
        assert "provider-2" in namespace.providers

    def test_effective_quota_without_override(self):
        """Effective quota returns tenant quota when no override."""
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
        )

        effective = namespace.get_effective_quota("max_providers", tenant_quota=50)

        assert effective == 50

    def test_effective_quota_with_override(self):
        """Effective quota uses override when set."""
        overrides = NamespaceQuotaOverrides(max_providers=30)
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
            quota_overrides=overrides,
        )

        effective = namespace.get_effective_quota("max_providers", tenant_quota=50)

        assert effective == 30  # Override is lower

    def test_effective_quota_capped_at_tenant_limit(self):
        """Override cannot exceed tenant quota."""
        overrides = NamespaceQuotaOverrides(max_providers=100)
        namespace = Namespace(
            namespace_id=NamespaceId("dev-env"),
            tenant_id=TenantId("test-team"),
            name="dev-env",
            display_name="Development Environment",
            created_by="user:admin@example.com",
            quota_overrides=overrides,
        )

        effective = namespace.get_effective_quota("max_providers", tenant_quota=50)

        assert effective == 50  # Capped at tenant limit


class TestValueObjects:
    """Tests for multi-tenancy value objects."""

    def test_tenant_id_validation(self):
        """TenantId validates naming rules."""
        # Valid
        TenantId("test-team")
        TenantId("my_tenant_123")

        # Invalid - doesn't start with letter
        with pytest.raises(ValueError, match="must start with a letter"):
            TenantId("123-tenant")

        # Invalid - too long
        with pytest.raises(ValueError, match="1-63 characters"):
            TenantId("a" * 64)

        # Invalid - special characters
        with pytest.raises(ValueError, match="alphanumeric"):
            TenantId("test@team")

    def test_namespace_id_validation(self):
        """NamespaceId validates Kubernetes naming rules."""
        # Valid
        NamespaceId("dev-env")
        NamespaceId("prod123")

        # Invalid - doesn't start/end with alphanumeric
        with pytest.raises(ValueError, match="start and end with alphanumeric"):
            NamespaceId("-dev-")

        # Invalid - underscore not allowed
        with pytest.raises(ValueError, match="alphanumeric with -"):
            NamespaceId("dev_env")
