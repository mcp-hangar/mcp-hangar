"""Integration tests for tool access policy filtering."""

import pytest

from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.domain.services import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.domain.value_objects import ToolAccessPolicy


@pytest.fixture(autouse=True)
def reset_resolver():
    """Reset global resolver before and after each test."""
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


class TestToolFilteringDiscovery:
    """Tests for tool filtering during discovery (hangar_tools, hangar_details)."""

    def test_deny_list_filters_tools_from_discovery(self):
        """Tools in deny_list should not appear in hangar_tools output."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*", "create_alert_rule")),
        )

        all_tools = [
            ToolSchema(name="get_dashboard", description="Get dashboard", input_schema={}),
            ToolSchema(name="delete_dashboard", description="Delete dashboard", input_schema={}),
            ToolSchema(name="create_alert_rule", description="Create alert", input_schema={}),
            ToolSchema(name="list_datasources", description="List datasources", input_schema={}),
        ]

        filtered = resolver.filter_tools("grafana", all_tools)

        assert len(filtered) == 2
        tool_names = [t.name for t in filtered]
        assert "get_dashboard" in tool_names
        assert "list_datasources" in tool_names
        assert "delete_dashboard" not in tool_names
        assert "create_alert_rule" not in tool_names

    def test_allow_list_shows_only_allowed_tools(self):
        """Only tools in allow_list should appear in hangar_tools output."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(allow_list=("get_*", "list_*")),
        )

        all_tools = [
            ToolSchema(name="get_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_dashboard", description="", input_schema={}),
            ToolSchema(name="list_datasources", description="", input_schema={}),
            ToolSchema(name="create_alert_rule", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools("grafana", all_tools)

        assert len(filtered) == 2
        tool_names = [t.name for t in filtered]
        assert "get_dashboard" in tool_names
        assert "list_datasources" in tool_names

    def test_group_policy_filters_tools(self):
        """Group policy should filter tools for all members."""
        resolver = get_tool_access_resolver()
        resolver.set_group_policy(
            "monitoring",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )
        resolver.set_member_policy("monitoring", "grafana-prod", ToolAccessPolicy(), mcp_server_id="grafana")

        all_tools = [
            ToolSchema(name="get_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_dashboard", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools(
            "grafana",
            all_tools,
            group_id="monitoring",
            member_id="grafana-prod",
        )

        assert len(filtered) == 1
        assert filtered[0].name == "get_dashboard"

    def test_all_tools_filtered_returns_empty_list(self):
        """If all tools are filtered, return empty list (not error)."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "restricted",
            ToolAccessPolicy(deny_list=("*",)),  # Deny everything
        )

        all_tools = [
            ToolSchema(name="tool1", description="", input_schema={}),
            ToolSchema(name="tool2", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools("restricted", all_tools)
        assert len(filtered) == 0

    def test_tool_count_reflects_filtered_count(self):
        """Tool count should reflect filtered count, not raw count."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )

        all_tools = [
            ToolSchema(name="get_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_alert", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools("grafana", all_tools)
        assert len(filtered) == 1  # Only get_dashboard


class TestToolFilteringInvocation:
    """Tests for tool filtering during invocation (hangar_call)."""

    def test_allowed_tool_passes_check(self):
        """Allowed tool should pass is_tool_allowed check."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )

        assert resolver.is_tool_allowed("grafana", "get_dashboard")
        assert resolver.is_tool_allowed("grafana", "create_dashboard")

    def test_denied_tool_fails_check(self):
        """Denied tool should fail is_tool_allowed check."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*", "create_alert_*")),
        )

        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")
        assert not resolver.is_tool_allowed("grafana", "create_alert_rule")

    def test_group_invocation_checks_policy(self):
        """Group invocation should check group+member policy."""
        resolver = get_tool_access_resolver()
        resolver.set_group_policy(
            "monitoring",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )
        resolver.set_member_policy(
            "monitoring",
            "grafana-prod",
            ToolAccessPolicy(deny_list=("create_alert_*",)),
            mcp_server_id="grafana",
        )

        # Should be denied by group policy
        assert not resolver.is_tool_allowed(
            "grafana", "delete_dashboard", group_id="monitoring", member_id="grafana-prod"
        )

        # Should be denied by member policy
        assert not resolver.is_tool_allowed(
            "grafana", "create_alert_rule", group_id="monitoring", member_id="grafana-prod"
        )

        # Should be allowed (not in any deny list)
        assert resolver.is_tool_allowed("grafana", "get_dashboard", group_id="monitoring", member_id="grafana-prod")


class TestToolFilteringReload:
    """Tests for tool filtering with config reload."""

    def test_policy_changes_take_effect_after_reload(self):
        """Changing policy should take effect immediately (cache invalidation)."""
        resolver = get_tool_access_resolver()

        # Initial policy
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )
        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")
        assert resolver.is_tool_allowed("grafana", "create_dashboard")

        # Change policy
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("create_*",)),
        )
        assert resolver.is_tool_allowed("grafana", "delete_dashboard")
        assert not resolver.is_tool_allowed("grafana", "create_dashboard")

    def test_add_policy_to_unrestricted_provider(self):
        """Adding policy to previously unrestricted provider should work."""
        resolver = get_tool_access_resolver()

        # No policy initially
        assert resolver.is_tool_allowed("grafana", "delete_dashboard")

        # Add policy
        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )
        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")

    def test_remove_policy_makes_all_visible(self):
        """Removing policy should make all tools visible again."""
        resolver = get_tool_access_resolver()

        resolver.set_mcp_server_policy(
            "grafana",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )
        assert not resolver.is_tool_allowed("grafana", "delete_dashboard")

        # Remove policy
        resolver.remove_provider_policy("grafana")
        assert resolver.is_tool_allowed("grafana", "delete_dashboard")


class TestToolFilteringHotLoad:
    """Tests for tool filtering with hot-loaded providers."""

    def test_hotloaded_provider_with_deny_tools(self):
        """Hot-loaded provider with deny_tools should filter correctly."""
        resolver = get_tool_access_resolver()

        # Simulate what hangar_load does with deny_tools
        policy = ToolAccessPolicy(deny_list=("create_*", "delete_*"))
        resolver.set_mcp_server_policy("mcp-server-grafana", policy)

        all_tools = [
            ToolSchema(name="get_dashboard", description="", input_schema={}),
            ToolSchema(name="create_dashboard", description="", input_schema={}),
            ToolSchema(name="delete_dashboard", description="", input_schema={}),
        ]

        filtered = resolver.filter_tools("mcp-server-grafana", all_tools)
        assert len(filtered) == 1
        assert filtered[0].name == "get_dashboard"

    def test_hotloaded_provider_with_allow_tools(self):
        """Hot-loaded provider with allow_tools should show only allowed."""
        resolver = get_tool_access_resolver()

        # Simulate what hangar_load does with allow_tools
        policy = ToolAccessPolicy(allow_list=("get_*",))
        resolver.set_mcp_server_policy("mcp-server-grafana", policy)

        assert resolver.is_tool_allowed("mcp-server-grafana", "get_dashboard")
        assert not resolver.is_tool_allowed("mcp-server-grafana", "create_dashboard")

    def test_unload_removes_policy(self):
        """hangar_unload should remove tool access policy."""
        resolver = get_tool_access_resolver()

        # Set policy
        resolver.set_mcp_server_policy(
            "mcp-server-time",
            ToolAccessPolicy(deny_list=("set_*",)),
        )
        assert not resolver.is_tool_allowed("mcp-server-time", "set_timezone")

        # Remove (simulating hangar_unload)
        resolver.remove_provider_policy("mcp-server-time")
        assert resolver.is_tool_allowed("mcp-server-time", "set_timezone")


class TestGlobPatterns:
    """Tests for glob pattern support."""

    def test_asterisk_wildcard(self):
        """Asterisk should match any characters."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "test",
            ToolAccessPolicy(deny_list=("delete_*",)),
        )

        assert not resolver.is_tool_allowed("test", "delete_dashboard")
        assert not resolver.is_tool_allowed("test", "delete_alert_rule")
        assert not resolver.is_tool_allowed("test", "delete_")
        assert resolver.is_tool_allowed("test", "create_dashboard")

    def test_middle_wildcard(self):
        """Wildcard in middle should match correctly."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "test",
            ToolAccessPolicy(deny_list=("*_alert_*",)),
        )

        assert not resolver.is_tool_allowed("test", "create_alert_rule")
        assert not resolver.is_tool_allowed("test", "delete_alert_rule")
        assert not resolver.is_tool_allowed("test", "get_alert_status")
        assert resolver.is_tool_allowed("test", "get_dashboard")

    def test_question_mark_single_char(self):
        """Question mark should match single character."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "test",
            ToolAccessPolicy(deny_list=("tool_?",)),
        )

        assert not resolver.is_tool_allowed("test", "tool_a")
        assert not resolver.is_tool_allowed("test", "tool_1")
        assert resolver.is_tool_allowed("test", "tool_ab")  # Two chars
        assert resolver.is_tool_allowed("test", "tool_")  # Zero chars

    def test_exact_name_match(self):
        """Exact names should match exactly."""
        resolver = get_tool_access_resolver()
        resolver.set_mcp_server_policy(
            "test",
            ToolAccessPolicy(deny_list=("dangerous_tool",)),
        )

        assert not resolver.is_tool_allowed("test", "dangerous_tool")
        assert resolver.is_tool_allowed("test", "dangerous_tool_v2")
        assert resolver.is_tool_allowed("test", "safe_tool")
