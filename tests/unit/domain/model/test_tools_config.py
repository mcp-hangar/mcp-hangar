"""Unit tests for ToolsConfig configuration model."""

import pytest

from mcp_hangar.domain.model.mcp_server_config import McpServerConfig, ToolsConfig


class TestToolsConfig:
    """Tests for ToolsConfig dataclass."""

    def test_empty_tools_config(self):
        """Empty ToolsConfig should have empty lists."""
        config = ToolsConfig()
        assert config.allow_list == []
        assert config.deny_list == []
        assert config.is_empty()

    def test_deny_list_only(self):
        """ToolsConfig with only deny_list."""
        config = ToolsConfig(deny_list=["delete_*", "create_alert_rule"])
        assert config.allow_list == []
        assert config.deny_list == ["delete_*", "create_alert_rule"]
        assert not config.is_empty()

    def test_allow_list_only(self):
        """ToolsConfig with only allow_list."""
        config = ToolsConfig(allow_list=["get_*", "list_*"])
        assert config.allow_list == ["get_*", "list_*"]
        assert config.deny_list == []
        assert not config.is_empty()

    def test_both_lists_logs_warning(self, caplog):
        """ToolsConfig with both lists should log warning."""
        _config = ToolsConfig(
            allow_list=["get_*"],
            deny_list=["delete_*"],
        )
        assert "allow_list takes precedence" in caplog.text

    def test_to_policy_empty(self):
        """Empty ToolsConfig should produce unrestricted policy."""
        config = ToolsConfig()
        policy = config.to_policy()
        assert policy.is_unrestricted()

    def test_to_policy_deny_list(self):
        """ToolsConfig deny_list should produce policy with deny_list."""
        config = ToolsConfig(deny_list=["delete_*"])
        policy = config.to_policy()
        assert policy.deny_list == ("delete_*",)
        assert policy.allow_list == ()
        assert not policy.is_tool_allowed("delete_dashboard")
        assert policy.is_tool_allowed("get_dashboard")

    def test_to_policy_allow_list(self):
        """ToolsConfig allow_list should produce policy with allow_list."""
        config = ToolsConfig(allow_list=["get_*", "list_*"])
        policy = config.to_policy()
        assert policy.allow_list == ("get_*", "list_*")
        assert policy.deny_list == ()
        assert policy.is_tool_allowed("get_dashboard")
        assert not policy.is_tool_allowed("delete_dashboard")

    def test_invalid_empty_pattern_raises(self):
        """Empty string pattern should raise ValueError."""
        with pytest.raises(ValueError):
            ToolsConfig(allow_list=["get_*", ""])

    def test_invalid_whitespace_pattern_raises(self):
        """Whitespace-only pattern should raise ValueError."""
        with pytest.raises(ValueError):
            ToolsConfig(deny_list=["   "])


class TestProviderConfigToolsAccess:
    """Tests for ProviderConfig.tools_access parsing."""

    def test_no_tools_config(self):
        """Provider config without tools should have no tools_access."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "subprocess",
                "command": ["python", "-m", "test"],
            },
        )
        assert config.tools_access is None
        assert config.get_tools_policy().is_unrestricted()

    def test_predefined_tools_list(self):
        """Provider config with tools as list should parse as predefined tools."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "subprocess",
                "command": ["python", "-m", "test"],
                "tools": [
                    {"name": "my_tool", "description": "A tool"},
                ],
            },
        )
        assert config.tools == [{"name": "my_tool", "description": "A tool"}]
        assert config.tools_access is None
        assert config.get_tools_policy().is_unrestricted()

    def test_tools_deny_list(self):
        """Provider config with tools.deny_list should parse as access policy."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "docker",
                "image": "test:latest",
                "tools": {
                    "deny_list": ["delete_*", "create_alert_rule"],
                },
            },
        )
        assert config.tools == []  # No predefined tools
        assert config.tools_access is not None
        assert config.tools_access.deny_list == ["delete_*", "create_alert_rule"]

        policy = config.get_tools_policy()
        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("create_alert_rule")
        assert policy.is_tool_allowed("get_dashboard")

    def test_tools_allow_list(self):
        """Provider config with tools.allow_list should parse as access policy."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "docker",
                "image": "test:latest",
                "tools": {
                    "allow_list": ["get_*", "list_*"],
                },
            },
        )
        assert config.tools == []
        assert config.tools_access is not None
        assert config.tools_access.allow_list == ["get_*", "list_*"]

        policy = config.get_tools_policy()
        assert policy.is_tool_allowed("get_dashboard")
        assert policy.is_tool_allowed("list_datasources")
        assert not policy.is_tool_allowed("delete_dashboard")

    def test_tools_both_lists(self, caplog):
        """Provider config with both allow and deny should log warning."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "docker",
                "image": "test:latest",
                "tools": {
                    "allow_list": ["get_*"],
                    "deny_list": ["delete_*"],  # Will be ignored
                },
            },
        )
        assert config.tools_access is not None
        assert "allow_list takes precedence" in caplog.text

    def test_tools_empty_dict(self):
        """Provider config with empty tools dict should have no access policy."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "docker",
                "image": "test:latest",
                "tools": {},
            },
        )
        assert config.tools == []
        assert config.tools_access is None

    def test_backward_compat_no_tools_key(self):
        """Existing configs without tools key should work unchanged."""
        config = McpServerConfig.from_dict(
            "test",
            {
                "mode": "subprocess",
                "command": ["python", "-m", "test"],
                "idle_ttl_s": 300,
            },
        )
        assert config.tools == []
        assert config.tools_access is None
        assert config.get_tools_policy().is_unrestricted()


class TestToolsConfigGlobPatterns:
    """Tests for glob pattern validation in ToolsConfig."""

    def test_valid_glob_patterns(self):
        """Valid glob patterns should be accepted."""
        config = ToolsConfig(
            deny_list=[
                "delete_*",
                "*_alert_*",
                "get_dashboard",
                "list_?",
            ]
        )
        policy = config.to_policy()

        assert not policy.is_tool_allowed("delete_dashboard")
        assert not policy.is_tool_allowed("create_alert_rule")
        assert not policy.is_tool_allowed("get_dashboard")
        assert not policy.is_tool_allowed("list_a")
        assert policy.is_tool_allowed("list_ab")  # ? matches single char
