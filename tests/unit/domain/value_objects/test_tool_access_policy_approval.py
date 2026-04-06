"""Unit tests for ToolAccessPolicy approval_list behavior."""

import pytest

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class TestRequiresApproval:
    """Tests for requires_approval() method."""

    def test_requires_approval_true_for_matching_tool(self):
        """Tool matching approval_list should require approval."""
        policy = ToolAccessPolicy(approval_list=("update_page", "create_page"))
        assert policy.requires_approval("update_page")
        assert policy.requires_approval("create_page")

    def test_requires_approval_false_for_non_matching_tool(self):
        """Tool not on approval_list should not require approval."""
        policy = ToolAccessPolicy(approval_list=("update_page",))
        assert not policy.requires_approval("search")

    def test_requires_approval_false_when_empty(self):
        """Empty approval_list means no tool requires approval."""
        policy = ToolAccessPolicy()
        assert not policy.requires_approval("any_tool")

    def test_deny_list_overrides_approval_list(self):
        """Denied tool returns False from requires_approval -- not allowed at all."""
        policy = ToolAccessPolicy(
            deny_list=("delete_database",),
            approval_list=("delete_*",),
        )
        assert not policy.requires_approval("delete_database")
        # But delete_page is not on deny_list, so it requires approval
        assert policy.requires_approval("delete_page")

    def test_glob_patterns_work_in_approval_list(self):
        """Glob patterns should match in approval_list."""
        policy = ToolAccessPolicy(approval_list=("delete_*", "*_alert_*"))
        assert policy.requires_approval("delete_page")
        assert policy.requires_approval("delete_database")
        assert policy.requires_approval("create_alert_rule")
        assert not policy.requires_approval("search")
        assert not policy.requires_approval("get_page")


class TestIsToolAllowedWithApproval:
    """Tests for is_tool_allowed() interaction with approval_list."""

    def test_approval_list_tools_are_allowed(self):
        """Tools on approval_list should return True from is_tool_allowed (visible)."""
        policy = ToolAccessPolicy(approval_list=("update_page", "create_page"))
        assert policy.is_tool_allowed("update_page")
        assert policy.is_tool_allowed("create_page")

    def test_deny_overrides_approval_in_is_tool_allowed(self):
        """Tool on both deny_list and approval_list is blocked."""
        policy = ToolAccessPolicy(
            deny_list=("delete_database",),
            approval_list=("delete_*",),
        )
        assert not policy.is_tool_allowed("delete_database")
        assert policy.is_tool_allowed("delete_page")

    def test_tool_not_on_any_list_is_allowed(self):
        """Tool not matching any list still allowed (unrestricted fallback)."""
        policy = ToolAccessPolicy(approval_list=("update_page",))
        assert policy.is_tool_allowed("search")

    def test_allow_list_with_approval_list(self):
        """When both allow_list and approval_list defined, deny > approval > allow."""
        policy = ToolAccessPolicy(
            allow_list=("search", "get_page"),
            approval_list=("update_page",),
            deny_list=("delete_*",),
        )
        assert policy.is_tool_allowed("search")
        assert policy.is_tool_allowed("get_page")
        assert policy.is_tool_allowed("update_page")  # approval => visible
        assert not policy.is_tool_allowed("delete_page")  # denied
        assert not policy.is_tool_allowed("create_page")  # not on allow or approval


class TestFilterToolsWithApproval:
    """Tests for filter_tools() with approval_list."""

    def test_filter_tools_includes_approval_list_tools(self):
        """filter_tools should include tools on approval_list (they're visible)."""
        policy = ToolAccessPolicy(
            allow_list=("search", "get_page"),
            approval_list=("update_page",),
        )
        tools = ["search", "get_page", "update_page", "delete_page"]
        filtered = policy.filter_tools(tools)
        assert "search" in filtered
        assert "get_page" in filtered
        assert "update_page" in filtered
        assert "delete_page" not in filtered


class TestIsUnrestrictedWithApproval:
    """Tests for is_unrestricted() with approval_list."""

    def test_not_unrestricted_when_only_approval_list_set(self):
        """Policy with only approval_list is not unrestricted."""
        policy = ToolAccessPolicy(approval_list=("update_page",))
        assert not policy.is_unrestricted()

    def test_unrestricted_when_all_empty(self):
        """Only unrestricted when all three lists are empty."""
        policy = ToolAccessPolicy()
        assert policy.is_unrestricted()


class TestApprovalListValidation:
    """Tests for approval_list pattern validation."""

    def test_empty_pattern_in_approval_list_raises(self):
        """Empty string pattern in approval_list should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid approval_list pattern"):
            ToolAccessPolicy(approval_list=("update_page", ""))

    def test_none_pattern_in_approval_list_raises(self):
        """None pattern in approval_list should raise ValueError."""
        with pytest.raises(ValueError):
            ToolAccessPolicy(approval_list=("update_page", None))  # type: ignore


class TestApprovalListMerge:
    """Tests for merge() behavior with approval_list."""

    def test_merge_approval_lists_produce_union(self):
        """Merging approval_lists from two scopes produces union."""
        broader = ToolAccessPolicy(approval_list=("update_page",))
        narrower = ToolAccessPolicy(approval_list=("create_page",))

        merged = ToolAccessPolicy.merge(broader, narrower)
        assert merged.requires_approval("update_page")
        assert merged.requires_approval("create_page")

    def test_broader_approval_survives_narrower_allow(self):
        """Broader scope approval_list cannot be overridden by narrower allow_list."""
        broader = ToolAccessPolicy(approval_list=("update_page",))
        narrower = ToolAccessPolicy(allow_list=("update_page", "search"))

        merged = ToolAccessPolicy.merge(broader, narrower)
        # update_page should still require approval even though narrower allows it
        assert merged.requires_approval("update_page")
        assert merged.is_tool_allowed("update_page")

    def test_narrower_approval_added_to_broader_allow(self):
        """Narrower scope can add approval requirement on top of broader allow."""
        broader = ToolAccessPolicy(allow_list=("get_*", "update_*"))
        narrower = ToolAccessPolicy(approval_list=("update_*",))

        merged = ToolAccessPolicy.merge(broader, narrower)
        assert merged.requires_approval("update_page")
        assert not merged.requires_approval("get_page")
        assert merged.is_tool_allowed("get_page")
        assert merged.is_tool_allowed("update_page")

    def test_merge_approval_list_grows_across_three_levels(self):
        """Three-level merge: approval_list only grows."""
        provider = ToolAccessPolicy(approval_list=("delete_*",))
        group = ToolAccessPolicy(approval_list=("update_*",))
        member = ToolAccessPolicy(approval_list=("create_*",))

        intermediate = ToolAccessPolicy.merge(provider, group)
        final = ToolAccessPolicy.merge(intermediate, member)

        assert final.requires_approval("delete_page")
        assert final.requires_approval("update_page")
        assert final.requires_approval("create_page")
        assert not final.requires_approval("search")

    def test_merge_deny_overrides_approval_in_merged(self):
        """Deny from broader scope still overrides approval in merged result."""
        broader = ToolAccessPolicy(deny_list=("delete_database",))
        narrower = ToolAccessPolicy(approval_list=("delete_*",))

        merged = ToolAccessPolicy.merge(broader, narrower)
        assert not merged.is_tool_allowed("delete_database")
        assert not merged.requires_approval("delete_database")

    def test_merge_unrestricted_with_approval_returns_approval(self):
        """Unrestricted broader + approval narrower = approval policy."""
        broader = ToolAccessPolicy()
        narrower = ToolAccessPolicy(approval_list=("update_page",))

        merged = ToolAccessPolicy.merge(broader, narrower)
        assert merged.requires_approval("update_page")
        assert not merged.requires_approval("search")

    def test_merge_approval_with_unrestricted_returns_approval(self):
        """Approval broader + unrestricted narrower = approval policy."""
        broader = ToolAccessPolicy(approval_list=("update_page",))
        narrower = ToolAccessPolicy()

        merged = ToolAccessPolicy.merge(broader, narrower)
        assert merged.requires_approval("update_page")


class TestApprovalListRepr:
    """Tests for __repr__() with approval_list."""

    def test_repr_includes_approval_list(self):
        """repr should show approval_list when non-empty."""
        policy = ToolAccessPolicy(approval_list=("update_page",))
        assert "approval" in repr(policy)
        assert "update_page" in repr(policy)

    def test_repr_shows_all_lists(self):
        """repr should show all non-empty lists."""
        policy = ToolAccessPolicy(
            allow_list=("search",),
            deny_list=("delete_*",),
            approval_list=("update_page",),
        )
        r = repr(policy)
        assert "allow" in r
        assert "deny" in r
        assert "approval" in r


class TestApprovalConfigFields:
    """Tests for approval_timeout_seconds and approval_channel fields."""

    def test_default_timeout(self):
        policy = ToolAccessPolicy()
        assert policy.approval_timeout_seconds == 300

    def test_default_channel(self):
        policy = ToolAccessPolicy()
        assert policy.approval_channel == "dashboard"

    def test_custom_timeout_and_channel(self):
        policy = ToolAccessPolicy(
            approval_list=("update_page",),
            approval_timeout_seconds=60,
            approval_channel="slack",
        )
        assert policy.approval_timeout_seconds == 60
        assert policy.approval_channel == "slack"

    def test_frozen_config_fields(self):
        """Config fields should be immutable (frozen dataclass)."""
        policy = ToolAccessPolicy(approval_timeout_seconds=60)
        with pytest.raises(AttributeError):
            policy.approval_timeout_seconds = 120  # type: ignore
