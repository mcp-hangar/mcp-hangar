"""Unit tests for ToolAccessResolver domain service."""

from concurrent.futures import ThreadPoolExecutor

import pytest

from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
    ToolAccessResolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy


@pytest.fixture
def resolver():
    """Create a fresh resolver for each test."""
    r = ToolAccessResolver()
    yield r
    r.clear_all()


@pytest.fixture(autouse=True)
def reset_global_resolver():
    """Reset global resolver before and after each test."""
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


class TestToolAccessResolverBasic:
    """Basic functionality tests."""

    def test_no_policy_returns_unrestricted(self, resolver):
        """Provider without policy should return unrestricted policy."""
        policy = resolver.resolve_effective_policy("test-provider")
        assert policy.is_unrestricted()

    def test_set_mcp_server_policy(self, resolver):
        """Setting provider policy should be retrievable."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy)

        effective = resolver.resolve_effective_policy("grafana")
        assert not effective.is_unrestricted()
        assert not effective.is_tool_allowed("delete_dashboard")
        assert effective.is_tool_allowed("get_dashboard")

    def test_remove_provider_policy(self, resolver):
        """Removing provider policy should return to unrestricted."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy)
        resolver.remove_provider_policy("grafana")

        effective = resolver.resolve_effective_policy("grafana")
        assert effective.is_unrestricted()

    def test_set_unrestricted_policy_removes_it(self, resolver):
        """Setting unrestricted policy should effectively remove it."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy)

        # Set unrestricted
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy())

        effective = resolver.resolve_effective_policy("grafana")
        assert effective.is_unrestricted()


class TestToolAccessResolverGroupMerge:
    """Tests for group-level policy merging."""

    def test_group_policy_applied_to_member(self, resolver):
        """Group policy should be applied to members."""
        group_policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_group_policy("monitoring", group_policy)
        resolver.set_member_policy("monitoring", "grafana-prod", ToolAccessPolicy(), mcp_server_id="grafana")

        effective = resolver.resolve_effective_policy("grafana", group_id="monitoring", member_id="grafana-prod")
        assert not effective.is_tool_allowed("delete_dashboard")
        assert effective.is_tool_allowed("get_dashboard")

    def test_member_policy_adds_restrictions(self, resolver):
        """Member policy should add restrictions on top of group policy."""
        group_policy = ToolAccessPolicy(deny_list=("delete_*",))
        member_policy = ToolAccessPolicy(deny_list=("create_alert_rule",))

        resolver.set_group_policy("monitoring", group_policy)
        resolver.set_member_policy("monitoring", "grafana-prod", member_policy, mcp_server_id="grafana")

        effective = resolver.resolve_effective_policy("grafana", group_id="monitoring", member_id="grafana-prod")
        # Both group and member denials should apply
        assert not effective.is_tool_allowed("delete_dashboard")
        assert not effective.is_tool_allowed("create_alert_rule")
        assert effective.is_tool_allowed("get_dashboard")

    def test_member_inherits_group_when_no_member_policy(self, resolver):
        """Member without own policy should inherit group policy only."""
        group_policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_group_policy("monitoring", group_policy)
        # No explicit member policy set
        resolver.set_member_policy("monitoring", "grafana-staging", ToolAccessPolicy(), mcp_server_id="grafana")

        effective = resolver.resolve_effective_policy("grafana", group_id="monitoring", member_id="grafana-staging")
        assert not effective.is_tool_allowed("delete_dashboard")
        assert effective.is_tool_allowed("get_dashboard")


class TestToolAccessResolverThreeLevelMerge:
    """Tests for three-level policy merge: provider -> group -> member."""

    def test_provider_group_member_merge(self, resolver):
        """All three levels should merge correctly."""
        provider_policy = ToolAccessPolicy(allow_list=("get_*", "list_*", "create_*", "update_*"))
        group_policy = ToolAccessPolicy(deny_list=("create_alert_*", "update_alert_*"))
        member_policy = ToolAccessPolicy(deny_list=("update_dashboard",))

        resolver.set_mcp_server_policy("grafana", provider_policy)
        resolver.set_group_policy("monitoring", group_policy)
        resolver.set_member_policy("monitoring", "grafana-prod", member_policy, mcp_server_id="grafana")

        effective = resolver.resolve_effective_policy("grafana", group_id="monitoring", member_id="grafana-prod")

        # Provider allows get_*, list_*, create_*, update_*
        # Group denies create_alert_*, update_alert_*
        # Member denies update_dashboard
        assert effective.is_tool_allowed("get_dashboard")
        assert effective.is_tool_allowed("list_datasources")
        assert effective.is_tool_allowed("create_dashboard")  # Not create_alert_*
        assert not effective.is_tool_allowed("create_alert_rule")  # Group deny
        assert not effective.is_tool_allowed("update_alert_rule")  # Group deny
        assert not effective.is_tool_allowed("update_dashboard")  # Member deny
        assert not effective.is_tool_allowed("delete_dashboard")  # Provider doesn't allow

    def test_standalone_provider_ignores_group_context(self, resolver):
        """Standalone provider should only use provider policy."""
        provider_policy = ToolAccessPolicy(deny_list=("delete_*",))
        group_policy = ToolAccessPolicy(deny_list=("create_*",))

        resolver.set_mcp_server_policy("math", provider_policy)
        resolver.set_group_policy("compute", group_policy)

        # Call without group context
        effective = resolver.resolve_effective_policy("math")
        assert not effective.is_tool_allowed("delete_value")
        assert effective.is_tool_allowed("create_value")  # Group policy not applied


class TestToolAccessResolverCache:
    """Tests for caching behavior."""

    def test_cache_hit(self, resolver):
        """Second call should use cached policy."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy)

        # First call computes and caches
        result1 = resolver.resolve_effective_policy("grafana")
        # Second call should hit cache
        result2 = resolver.resolve_effective_policy("grafana")

        assert result1 is result2  # Same object from cache

    def test_cache_invalidation_on_policy_change(self, resolver):
        """Changing policy should invalidate cache."""
        policy1 = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy1)

        result1 = resolver.resolve_effective_policy("grafana")
        assert not result1.is_tool_allowed("delete_dashboard")

        # Change policy
        policy2 = ToolAccessPolicy(deny_list=("create_*",))
        resolver.set_mcp_server_policy("grafana", policy2)

        result2 = resolver.resolve_effective_policy("grafana")
        assert result2.is_tool_allowed("delete_dashboard")
        assert not result2.is_tool_allowed("create_dashboard")

    def test_cache_invalidation_explicit(self, resolver):
        """Explicit invalidation should clear cache and force recomputation."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        resolver.set_mcp_server_policy("grafana", policy)

        # First resolve - populates cache
        result1 = resolver.resolve_effective_policy("grafana")

        # Verify cache is populated
        assert len(resolver._policy_cache) == 1

        # Invalidate cache
        resolver.invalidate_cache("grafana")

        # Verify cache is cleared
        assert len(resolver._policy_cache) == 0

        # Second resolve - should recompute
        result2 = resolver.resolve_effective_policy("grafana")

        # Verify cache is repopulated
        assert len(resolver._policy_cache) == 1

        # Both results should behave the same (same policy)
        assert result1.is_tool_allowed("get_dashboard") == result2.is_tool_allowed("get_dashboard")

    def test_cache_invalidation_all(self, resolver):
        """Invalidating all should clear entire cache."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("delete_*",)))
        resolver.set_mcp_server_policy("prometheus", ToolAccessPolicy(deny_list=("create_*",)))

        resolver.resolve_effective_policy("grafana")
        resolver.resolve_effective_policy("prometheus")

        resolver.invalidate_cache(None)  # Invalidate all

        # Both should be recomputed
        assert len(resolver._policy_cache) == 0


class TestToolAccessResolverFiltering:
    """Tests for tool filtering methods."""

    def test_is_tool_allowed(self, resolver):
        """is_tool_allowed should check against effective policy."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("delete_*",)))

        assert resolver.is_tool_allowed("grafana", "get_dashboard")
        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")

    def test_filter_tools(self, resolver):
        """filter_tools should return only allowed ToolSchema objects."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("delete_*", "create_alert_*")))

        tools = [
            ToolSchema(name="get_dashboard", description="Get dashboard", input_schema={}),
            ToolSchema(name="delete_dashboard", description="Delete dashboard", input_schema={}),
            ToolSchema(name="create_alert_rule", description="Create alert", input_schema={}),
            ToolSchema(name="list_datasources", description="List datasources", input_schema={}),
        ]

        filtered = resolver.filter_tools("grafana", tools)

        assert len(filtered) == 2
        assert filtered[0].name == "get_dashboard"
        assert filtered[1].name == "list_datasources"

    def test_filter_tool_dicts(self, resolver):
        """filter_tool_dicts should return only allowed tool dictionaries."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(allow_list=("get_*", "list_*")))

        tools = [
            {"name": "get_dashboard", "description": "Get dashboard"},
            {"name": "delete_dashboard", "description": "Delete dashboard"},
            {"name": "list_datasources", "description": "List datasources"},
        ]

        filtered = resolver.filter_tool_dicts("grafana", tools)

        assert len(filtered) == 2
        assert filtered[0]["name"] == "get_dashboard"
        assert filtered[1]["name"] == "list_datasources"

    def test_filter_with_unrestricted_returns_all(self, resolver):
        """Filtering with unrestricted policy should return all tools."""
        tools = [
            ToolSchema(name="get_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_dashboard", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools("unrestricted-provider", tools)
        assert len(filtered) == 2


class TestToolAccessResolverThreadSafety:
    """Tests for thread safety."""

    def test_concurrent_resolve_calls(self, resolver):
        """Concurrent resolve calls should not corrupt state."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("delete_*",)))

        results = []

        def resolve():
            policy = resolver.resolve_effective_policy("grafana")
            results.append(policy.is_tool_allowed("get_dashboard"))
            results.append(not policy.is_tool_allowed("delete_dashboard"))

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(resolve) for _ in range(100)]
            for f in futures:
                f.result()

        # All results should be True (correct behavior)
        assert all(results)

    def test_concurrent_policy_updates(self, resolver):
        """Concurrent policy updates should not corrupt state."""

        def update_and_check(i):
            policy = ToolAccessPolicy(deny_list=(f"tool_{i}",))
            resolver.set_mcp_server_policy("test", policy)
            effective = resolver.resolve_effective_policy("test")
            # Just verify it doesn't raise
            return effective is not None

        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(update_and_check, i) for i in range(50)]
            results = [f.result() for f in futures]

        assert all(results)


class TestToolAccessResolverGlobalSingleton:
    """Tests for global singleton access."""

    def test_get_returns_same_instance(self):
        """get_tool_access_resolver should return same instance."""
        resolver1 = get_tool_access_resolver()
        resolver2 = get_tool_access_resolver()
        assert resolver1 is resolver2

    def test_reset_creates_new_instance(self):
        """reset_tool_access_resolver should create new instance."""
        resolver1 = get_tool_access_resolver()
        resolver1.set_mcp_server_policy("test", ToolAccessPolicy(deny_list=("x",)))

        reset_tool_access_resolver()

        resolver2 = get_tool_access_resolver()
        # New instance should not have the policy
        assert resolver2.resolve_effective_policy("test").is_unrestricted()


class TestToolAccessResolverPolicySummary:
    """Tests for get_policy_summary method."""

    def test_summary_no_policy(self, resolver):
        """Summary for provider without policy."""
        summary = resolver.get_policy_summary("unknown")
        assert summary["active"] is False
        assert summary["unrestricted"] is True

    def test_summary_with_deny_list(self, resolver):
        """Summary for provider with deny_list."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("delete_*",)))
        summary = resolver.get_policy_summary("grafana")
        assert summary["active"] is True
        assert summary["unrestricted"] is False
        assert summary["has_allow_list"] is False
        assert summary["has_deny_list"] is True

    def test_summary_with_allow_list(self, resolver):
        """Summary for provider with allow_list."""
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(allow_list=("get_*",)))
        summary = resolver.get_policy_summary("grafana")
        assert summary["active"] is True
        assert summary["unrestricted"] is False
        assert summary["has_allow_list"] is True
        assert summary["has_deny_list"] is False


class TestToolAccessResolverGlobalPolicy:
    """Tests for _global policy fallback (agent-pushed global deny/allow)."""

    def test_global_deny_applies_to_provider_without_policy(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(deny_list=("delete_*",)))

        assert not resolver.is_tool_allowed("any-provider", "delete_something")
        assert resolver.is_tool_allowed("any-provider", "get_something")

    def test_global_allow_list_applies_to_provider_without_policy(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(allow_list=("get_*", "list_*")))

        assert resolver.is_tool_allowed("any-provider", "get_data")
        assert not resolver.is_tool_allowed("any-provider", "delete_data")

    def test_provider_policy_merges_with_global(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(deny_list=("delete_*",)))
        resolver.set_mcp_server_policy("grafana", ToolAccessPolicy(deny_list=("create_alert_*",)))

        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")
        assert not resolver.is_tool_allowed("grafana", "create_alert_rule")
        assert resolver.is_tool_allowed("grafana", "get_dashboard")

    def test_global_deny_cannot_be_overridden_by_provider_allow(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(deny_list=("rm_*",)))
        resolver.set_mcp_server_policy("dangerous", ToolAccessPolicy(allow_list=("rm_prod", "rm_staging")))

        assert not resolver.is_tool_allowed("dangerous", "rm_prod")
        assert not resolver.is_tool_allowed("dangerous", "rm_staging")

    def test_global_allow_list_intersects_with_provider_allow_list(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(allow_list=("get_*", "add")))
        resolver.set_mcp_server_policy("math", ToolAccessPolicy(allow_list=("add", "multiply")))

        assert resolver.is_tool_allowed("math", "add")
        assert not resolver.is_tool_allowed("math", "multiply")
        assert not resolver.is_tool_allowed("math", "get_data")

    def test_no_policy_without_global_is_still_unrestricted(self, resolver):
        policy = resolver.resolve_effective_policy("provider-with-no-policy")
        assert policy.is_unrestricted()

    def test_global_approval_list_applies_to_provider_without_policy(self, resolver):
        resolver.set_mcp_server_policy("_global", ToolAccessPolicy(approval_list=("power_*",)))
        policy = resolver.resolve_effective_policy("any-provider")

        assert policy.requires_approval("power_delete")
        assert not policy.requires_approval("get_data")
