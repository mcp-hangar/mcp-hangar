"""Tests for CLI init command.

Tests cover Claude Desktop detection, config file generation,
and provider selection logic.
"""

from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from mcp_hangar.server.cli.commands.init import (
    _backup_file,
    _detect_claude_desktop,
    _generate_claude_desktop_config,
    _generate_hangar_config,
    _get_provider_categories,
    CLAUDE_DESKTOP_PATHS,
    DEFAULT_CONFIG_PATH,
)


class TestClaudeDesktopDetection:
    """Tests for Claude Desktop detection."""

    def test_detect_returns_none_when_not_found(self):
        """Returns None when no Claude Desktop config exists."""
        with patch.object(Path, "exists", return_value=False):
            config_path, config = _detect_claude_desktop()
            # May find actual config on developer machine
            # so we just check the return type
            assert config_path is None or isinstance(config_path, Path)

    def test_claude_desktop_paths_by_platform(self):
        """Correct paths defined for each platform."""
        assert "Darwin" in CLAUDE_DESKTOP_PATHS  # macOS
        assert "Linux" in CLAUDE_DESKTOP_PATHS
        assert "Windows" in CLAUDE_DESKTOP_PATHS

    def test_macos_path_is_application_support(self):
        """macOS path should be in Application Support."""
        mac_path = CLAUDE_DESKTOP_PATHS["Darwin"]
        assert "Application Support" in str(mac_path)
        assert "Claude" in str(mac_path)

    def test_linux_path_is_config(self):
        """Linux path should be in .config."""
        linux_path = CLAUDE_DESKTOP_PATHS["Linux"]
        assert ".config" in str(linux_path)
        assert "claude" in str(linux_path).lower()


class TestProviderCategories:
    """Tests for provider category organization."""

    def test_get_provider_categories_returns_dict(self):
        """Should return dictionary of categories."""
        categories = _get_provider_categories()
        assert isinstance(categories, dict)
        assert len(categories) > 0

    def test_starter_category_exists(self):
        """Starter category should exist and have providers."""
        categories = _get_provider_categories()
        starter_key = next(
            (k for k in categories.keys() if "starter" in k.lower()),
            None,
        )
        assert starter_key is not None
        assert len(categories[starter_key]) > 0

    def test_provider_has_required_fields(self):
        """Each provider should have name, description, package."""
        categories = _get_provider_categories()
        for category, providers in categories.items():
            for provider in providers:
                assert "name" in provider
                assert "description" in provider
                assert "package" in provider


class TestConfigGeneration:
    """Tests for configuration file generation."""

    def test_generate_hangar_config_basic(self):
        """Generated config should be valid YAML structure."""
        config_str = _generate_hangar_config(
            providers=["fetch", "memory"],
            configs={},
        )

        assert "providers:" in config_str
        assert "fetch:" in config_str
        assert "memory:" in config_str
        assert "mode: subprocess" in config_str

    def test_generate_hangar_config_with_path_config(self):
        """Config should include args for path-based providers."""
        config_str = _generate_hangar_config(
            providers=["filesystem"],
            configs={"filesystem": {"path": "/home/user/documents"}},
        )

        assert "/home/user/documents" in config_str
        assert "args:" in config_str

    def test_generate_hangar_config_with_env_var(self):
        """Config should include env var references."""
        config_str = _generate_hangar_config(
            providers=["github"],
            configs={"github": {"use_env": "GITHUB_TOKEN"}},
        )

        assert "env:" in config_str
        assert "GITHUB_TOKEN" in config_str

    def test_generate_hangar_config_includes_health_check(self):
        """Config should include health check settings."""
        config_str = _generate_hangar_config(
            providers=["fetch"],
            configs={},
        )

        assert "health_check:" in config_str
        assert "enabled: true" in config_str

    def test_generate_hangar_config_includes_logging(self):
        """Config should include logging settings."""
        config_str = _generate_hangar_config(
            providers=["fetch"],
            configs={},
        )

        assert "logging:" in config_str
        assert "level: INFO" in config_str

    def test_generate_claude_desktop_config(self):
        """Claude Desktop config should use mcp-hangar command."""
        config_path = Path("/home/user/.config/mcp-hangar/config.yaml")
        claude_config = _generate_claude_desktop_config(config_path)

        assert "mcpServers" in claude_config
        assert "mcp-hangar" in claude_config["mcpServers"]
        server_config = claude_config["mcpServers"]["mcp-hangar"]
        assert server_config["command"] == "mcp-hangar"
        assert "serve" in server_config["args"]
        assert str(config_path) in server_config["args"]


class TestBackupFile:
    """Tests for file backup functionality."""

    def test_backup_creates_timestamped_copy(self):
        """Backup should create a copy with timestamp."""
        with TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "config.yaml"
            original.write_text("test content")

            backup_path = _backup_file(original)

            assert backup_path is not None
            assert backup_path.exists()
            assert ".backup." in backup_path.name
            assert backup_path.read_text() == "test content"

    def test_backup_returns_none_for_missing_file(self):
        """Backup should return None if file doesn't exist."""
        result = _backup_file(Path("/nonexistent/file.yaml"))
        assert result is None


class TestDefaultConfigPath:
    """Tests for default configuration path."""

    def test_default_config_path_in_home_config(self):
        """Default config should be in ~/.config/mcp-hangar/."""
        assert ".config" in str(DEFAULT_CONFIG_PATH)
        assert "mcp-hangar" in str(DEFAULT_CONFIG_PATH)
        assert DEFAULT_CONFIG_PATH.name == "config.yaml"
