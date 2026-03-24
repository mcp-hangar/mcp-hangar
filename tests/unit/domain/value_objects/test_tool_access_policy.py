"""Unit tests for ToolAccessPolicy value object."""

import pytest

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class TestToolAccessPolicyBasic:
    """Basic functionality tests."""

    def test_empty_policy_allows_all_tools(self):
        """Empty policy (no allow/deny) should allow all tools."""
        policy = ToolAccessPolicy()
        assert policy.is_tool_allowed("any_tool")
        assert policy.is_tool_allowed("create_dashboard")
        assert policy.is_tool_allowed("delete_alert_rule")

    def test_empty_policy_is_unrestricted(self):
        """Empty policy should report as unrestricted."""
        policy = ToolAccessPolicy()
        assert policy.is_unrestricted()

    def test_empty_policy_filter_returns_all(self):
        """Empty policy filter should return all tools."""
        policy = ToolAccessPolicy()
        tools = ["create_dashboard", "delete_alert_rule", "get_status"]
        assert policy.filter_tools(tools) == tools


class TestToolAccessPolicyAllowList:
    """Tests for allow_list behavior."""

    def test_allow_list_only_allows_matching_tools(self):
        """Only tools matching allow_list patterns should be allowed."""
        policy = ToolAccessPolicy(allow_list=("get_dashboard", "list_datasources"))
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("list_datasources")
        assert not policy.is_tool_allowed("create_dashboard")
        assert not policy.is_tool_allowed("delete_alert_rule")

    def test_allow_list_is_not_unrestricted(self):
        """Policy with allow_list should not be unrestricted."""
        policy = ToolAccessPolicy(allow_list=("get_*",))
        assert not policy.is_unrestricted()

    def test_allow_list_filter_returns_only_matching(self):
        """Filter should return only tools matching allow_list."""
        policy = ToolAccessPolicy(allow_list=("get_dashboard", "list_datasources"))
        tools = ["get_dashboard", "create_dashboard", "list_datasources", "delete_alert"]
        assert policy.filter_tools(tools) == ["get_dashboard", "list_datasources"]

    def test_allow_list_with_glob_pattern(self):
        """Glob patterns should work in allow_list."""
        policy = ToolAccessPolicy(allow_list=("get_*", "list_*"))
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("get_alert_status")
        assert policy.is_tool_allowed("list_datasources")
        assert not policy.is_tool_allowed("create_dashboard")
        assert not policy.is_tool_allowed("delete_alert_rule")

    def test_allow_list_complex_glob(self):
        """Complex glob patterns should work."""
        policy = ToolAccessPolicy(allow_list=("*_dashboard", "*_alert_*"))
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("create_dashboard")
        assert policy.is_tool_allowed("get_alert_status")
        assert policy.is_tool_allowed("delete_alert_rule")
        assert not policy.is_tool_allowed("list_datasources")


class TestToolAccessPolicyDenyList:
    """Tests for deny_list behavior."""

    def test_deny_list_blocks_matching_tools(self):
        """Tools matching deny_list patterns should be blocked."""
        policy = ToolAccessPolicy(deny_list=("delete_*", "create_alert_rule"))
        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("delete_alert_rule")
        assert not policy.is_tool_allowed("create_alert_rule")
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("create_dashboard")

    def test_deny_list_is_not_unrestricted(self):
        """Policy with deny_list should not be unrestricted."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        assert not policy.is_unrestricted()

    def test_deny_list_filter_removes_matching(self):
        """Filter should remove tools matching deny_list."""
        policy = ToolAccessPolicy(deny_list=("delete_*", "create_alert_rule"))
        tools = ["get_dashboard", "delete_dashboard", "create_alert_rule", "list_datasources"]
        assert policy.filter_tools(tools) == ["get_dashboard", "list_datasources"]

    def test_deny_list_with_glob_pattern(self):
        """Glob patterns should work in deny_list."""
        policy = ToolAccessPolicy(deny_list=("delete_*", "*_alert_*"))
        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("create_alert_rule")
        assert not policy.is_tool_allowed("get_alert_status")
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("list_datasources")


class TestToolAccessPolicyAllowDenyPrecedence:
    """Tests for allow_list vs deny_list precedence."""

    def test_allow_list_takes_precedence_over_deny_list(self):
        """When both are defined, allow_list should take precedence (deny_list ignored)."""
        policy = ToolAccessPolicy(
            allow_list=("get_*", "list_*"),
            deny_list=("get_dashboard",),  # Should be ignored
        )
        # allow_list wins - get_dashboard matches get_* so it's allowed
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("list_datasources")
        # These don't match allow_list, so blocked
        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("create_alert_rule")


class TestToolAccessPolicyMerge:
    """Tests for policy merge behavior."""

    def test_merge_unrestricted_with_restricted_returns_restricted(self):
        """Merging unrestricted with restricted should return restricted."""
        unrestricted = ToolAccessPolicy()
        restricted = ToolAccessPolicy(deny_list=("delete_*",))

        merged = ToolAccessPolicy.merge(unrestricted, restricted)
        assert not merged.is_unrestricted()
        assert not merged.is_tool_allowed("delete_dashboard")
        assert merged.is_tool_allowed("get_dashboard")

    def test_merge_restricted_with_unrestricted_returns_restricted(self):
        """Merging restricted with unrestricted (passthrough) should return restricted."""
        restricted = ToolAccessPolicy(deny_list=("delete_*",))
        unrestricted = ToolAccessPolicy()

        merged = ToolAccessPolicy.merge(restricted, unrestricted)
        assert not merged.is_unrestricted()
        assert not merged.is_tool_allowed("delete_dashboard")
        assert merged.is_tool_allowed("get_dashboard")

    def test_merge_two_deny_lists_produces_union(self):
        """Merging two deny_lists should produce union of denials."""
        broader = ToolAccessPolicy(deny_list=("delete_*",))
        narrower = ToolAccessPolicy(deny_list=("create_alert_rule",))

        merged = ToolAccessPolicy.merge(broader, narrower)

        # Both denials should apply
        assert not merged.is_tool_allowed("delete_dashboard")
        assert not merged.is_tool_allowed("delete_alert_rule")
        assert not merged.is_tool_allowed("create_alert_rule")
        # Other tools should be allowed
        assert merged.is_tool_allowed("get_dashboard")
        assert merged.is_tool_allowed("create_dashboard")

    def test_merge_two_allow_lists_produces_intersection(self):
        """Merging two allow_lists should produce intersection."""
        broader = ToolAccessPolicy(allow_list=("get_*", "list_*", "create_dashboard"))
        narrower = ToolAccessPolicy(allow_list=("get_dashboard", "get_alert_status"))

        merged = ToolAccessPolicy.merge(broader, narrower)

        # Only tools matching BOTH should pass
        assert merged.is_tool_allowed("get_dashboard")  # matches both
        assert merged.is_tool_allowed("get_alert_status")  # matches both
        assert not merged.is_tool_allowed("get_other")  # matches broader but not narrower
        assert not merged.is_tool_allowed("list_datasources")  # matches broader but not narrower
        assert not merged.is_tool_allowed("create_dashboard")  # matches broader but not narrower

    def test_merge_allow_then_deny(self):
        """Broader allow_list + narrower deny_list: allow filtered by deny."""
        broader = ToolAccessPolicy(allow_list=("get_*", "list_*"))
        narrower = ToolAccessPolicy(deny_list=("get_secret_*",))

        merged = ToolAccessPolicy.merge(broader, narrower)

        # Must match allow AND not match deny
        assert merged.is_tool_allowed("get_dashboard")
        assert merged.is_tool_allowed("list_datasources")
        assert not merged.is_tool_allowed("get_secret_key")  # denied
        assert not merged.is_tool_allowed("create_dashboard")  # not in allow

    def test_merge_deny_then_allow(self):
        """Broader deny_list + narrower allow_list: allow within non-denied set."""
        broader = ToolAccessPolicy(deny_list=("delete_*",))
        narrower = ToolAccessPolicy(allow_list=("get_dashboard", "create_dashboard"))

        merged = ToolAccessPolicy.merge(broader, narrower)

        # Must NOT match deny AND must match allow
        assert merged.is_tool_allowed("get_dashboard")
        assert merged.is_tool_allowed("create_dashboard")
        assert not merged.is_tool_allowed("delete_dashboard")  # denied by broader
        assert not merged.is_tool_allowed("list_datasources")  # not in narrower allow

    def test_merge_invariant_holds(self):
        """Merged policy should produce same result as sequential application."""
        broader = ToolAccessPolicy(deny_list=("delete_*", "create_alert_*"))
        narrower = ToolAccessPolicy(deny_list=("update_*",))

        merged = ToolAccessPolicy.merge(broader, narrower)

        tools = [
            "get_dashboard",
            "delete_dashboard",
            "create_alert_rule",
            "update_dashboard",
            "list_datasources",
        ]

        # Sequential application
        sequential_result = narrower.filter_tools(broader.filter_tools(tools))

        # Merged application
        merged_result = merged.filter_tools(tools)

        assert merged_result == sequential_result

    def test_merge_invariant_with_allow_lists(self):
        """Merged policy invariant should hold with allow_lists."""
        broader = ToolAccessPolicy(allow_list=("get_*", "list_*", "create_*"))
        narrower = ToolAccessPolicy(allow_list=("get_dashboard", "get_alert", "list_*"))

        merged = ToolAccessPolicy.merge(broader, narrower)

        tools = [
            "get_dashboard",
            "get_alert",
            "get_other",
            "list_datasources",
            "create_dashboard",
            "delete_dashboard",
        ]

        sequential_result = narrower.filter_tools(broader.filter_tools(tools))
        merged_result = merged.filter_tools(tools)

        assert merged_result == sequential_result

    def test_merge_invariant_with_mixed_policies(self):
        """Merged policy invariant should hold with mixed allow/deny."""
        broader = ToolAccessPolicy(allow_list=("get_*", "list_*", "create_*"))
        narrower = ToolAccessPolicy(deny_list=("create_alert_*",))

        merged = ToolAccessPolicy.merge(broader, narrower)

        tools = [
            "get_dashboard",
            "list_datasources",
            "create_dashboard",
            "create_alert_rule",
            "delete_dashboard",
        ]

        sequential_result = narrower.filter_tools(broader.filter_tools(tools))
        merged_result = merged.filter_tools(tools)

        assert merged_result == sequential_result


class TestToolAccessPolicyThreeLevelMerge:
    """Tests for three-level merge (provider -> group -> member)."""

    def test_three_level_merge_all_deny(self):
        """Three-level merge with all deny_lists."""
        provider_policy = ToolAccessPolicy(deny_list=("delete_*",))
        group_policy = ToolAccessPolicy(deny_list=("create_alert_*",))
        member_policy = ToolAccessPolicy(deny_list=("update_dashboard",))

        # Merge provider -> group
        intermediate = ToolAccessPolicy.merge(provider_policy, group_policy)
        # Merge intermediate -> member
        final = ToolAccessPolicy.merge(intermediate, member_policy)

        tools = [
            "get_dashboard",
            "delete_dashboard",
            "create_alert_rule",
            "update_dashboard",
            "create_dashboard",
        ]

        # All denials should accumulate
        assert final.filter_tools(tools) == ["get_dashboard", "create_dashboard"]

    def test_three_level_merge_mixed_policies(self):
        """Three-level merge with mixed allow/deny."""
        provider_policy = ToolAccessPolicy(allow_list=("get_*", "list_*", "create_*", "update_*"))
        group_policy = ToolAccessPolicy(deny_list=("create_alert_*", "update_alert_*"))
        member_policy = ToolAccessPolicy(deny_list=("update_dashboard",))

        intermediate = ToolAccessPolicy.merge(provider_policy, group_policy)
        final = ToolAccessPolicy.merge(intermediate, member_policy)

        tools = [
            "get_dashboard",
            "list_datasources",
            "create_dashboard",
            "create_alert_rule",
            "update_dashboard",
            "update_alert_rule",
            "delete_dashboard",
        ]

        # Sequential application for comparison
        step1 = provider_policy.filter_tools(tools)
        step2 = group_policy.filter_tools(step1)
        step3 = member_policy.filter_tools(step2)

        assert final.filter_tools(tools) == step3


class TestToolAccessPolicyGlobPatterns:
    """Tests for glob pattern matching."""

    def test_asterisk_matches_any_suffix(self):
        """'delete_*' should match any tool starting with 'delete_'."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("delete_alert_rule")
        assert not policy.is_tool_allowed("delete_")
        assert policy.is_tool_allowed("delete")  # No underscore

    def test_asterisk_matches_any_prefix(self):
        """'*_dashboard' should match any tool ending with '_dashboard'."""
        policy = ToolAccessPolicy(allow_list=("*_dashboard",))
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("create_dashboard")
        assert policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("dashboard")
        assert not policy.is_tool_allowed("dashboard_get")

    def test_asterisk_in_middle(self):
        """'*_alert_*' should match tools with '_alert_' in the middle."""
        policy = ToolAccessPolicy(deny_list=("*_alert_*",))
        assert not policy.is_tool_allowed("create_alert_rule")
        assert not policy.is_tool_allowed("delete_alert_rule")
        assert not policy.is_tool_allowed("get_alert_status")
        assert policy.is_tool_allowed("alert_create")  # No underscore before 'alert'
        assert policy.is_tool_allowed("get_alerts")  # No underscore after 'alert'

    def test_question_mark_matches_single_char(self):
        """'get_?' should match 'get_' followed by exactly one character."""
        policy = ToolAccessPolicy(allow_list=("get_?",))
        assert policy.is_tool_allowed("get_a")
        assert policy.is_tool_allowed("get_1")
        assert not policy.is_tool_allowed("get_ab")
        assert not policy.is_tool_allowed("get_")

    def test_exact_match_works(self):
        """Exact tool names should work without patterns."""
        policy = ToolAccessPolicy(allow_list=("get_dashboard", "list_datasources"))
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("list_datasources")
        assert not policy.is_tool_allowed("get_dashboards")  # Extra 's'


class TestToolAccessPolicyImmutability:
    """Tests for immutability."""

    def test_policy_is_frozen(self):
        """Policy should be immutable (frozen dataclass)."""
        policy = ToolAccessPolicy(allow_list=("get_*",))

        with pytest.raises(AttributeError):
            policy.allow_list = ("other",)

        with pytest.raises(AttributeError):
            policy.deny_list = ("other",)

    def test_tuple_fields_are_immutable(self):
        """Tuple fields should prevent modification of contents."""
        policy = ToolAccessPolicy(allow_list=("get_*",))

        # Tuples are immutable, so we can't modify them
        # This is enforced by the tuple type itself
        assert isinstance(policy.allow_list, tuple)
        assert isinstance(policy.deny_list, tuple)


class TestToolAccessPolicyValidation:
    """Tests for input validation."""

    def test_empty_pattern_in_allow_list_raises(self):
        """Empty string pattern should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid allow_list pattern"):
            ToolAccessPolicy(allow_list=("get_*", ""))

    def test_empty_pattern_in_deny_list_raises(self):
        """Empty string pattern should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid deny_list pattern"):
            ToolAccessPolicy(deny_list=("delete_*", ""))

    def test_none_pattern_raises(self):
        """None pattern should raise ValueError."""
        with pytest.raises(ValueError):
            ToolAccessPolicy(allow_list=("get_*", None))  # type: ignore


class TestToolAccessPolicyRepr:
    """Tests for string representation."""

    def test_unrestricted_repr(self):
        """Unrestricted policy should have clear repr."""
        policy = ToolAccessPolicy()
        assert "unrestricted" in repr(policy)

    def test_allow_list_repr(self):
        """Allow list policy should show allow patterns."""
        policy = ToolAccessPolicy(allow_list=("get_*",))
        assert "allow" in repr(policy)
        assert "get_*" in repr(policy)

    def test_deny_list_repr(self):
        """Deny list policy should show deny patterns."""
        policy = ToolAccessPolicy(deny_list=("delete_*",))
        assert "deny" in repr(policy)
        assert "delete_*" in repr(policy)
