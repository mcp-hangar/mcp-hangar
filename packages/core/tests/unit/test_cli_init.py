"""Tests for CLI init command.

Tests cover Claude Desktop detection, config file generation,
and provider selection logic.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_hangar.server.cli.services import (
    ClaudeDesktopManager,
    ConfigFileManager,
    get_provider,
    get_providers_by_category,
)


class TestClaudeDesktopManager:
    """Tests for Claude Desktop detection."""

    def test_platform_paths_by_platform(self):
        """Correct paths defined for each platform."""
        assert "Darwin" in ClaudeDesktopManager.PLATFORM_PATHS  # macOS
        assert "Linux" in ClaudeDesktopManager.PLATFORM_PATHS
        assert "Windows" in ClaudeDesktopManager.PLATFORM_PATHS

    def test_macos_path_is_application_support(self):
        """macOS path should be in Application Support."""
        mac_path = ClaudeDesktopManager.PLATFORM_PATHS["Darwin"]
        assert "Application Support" in str(mac_path)
        assert "Claude" in str(mac_path)

    def test_linux_path_is_config(self):
        """Linux path should be in .config."""
        linux_path = ClaudeDesktopManager.PLATFORM_PATHS["Linux"]
        assert ".config" in str(linux_path)
        assert "claude" in str(linux_path).lower()

    def test_backup_returns_none_for_missing_file(self):
        """Backup should return None if file doesn't exist."""
        manager = ClaudeDesktopManager(Path("/nonexistent/file.json"))
        result = manager.backup()
        assert result is None


class TestProviderCategories:
    """Tests for provider category organization."""

    def test_get_providers_by_category_returns_dict(self):
        """Should return dictionary of categories."""
        categories = get_providers_by_category()
        assert isinstance(categories, dict)
        assert len(categories) > 0

    def test_starter_category_exists(self):
        """Starter category should exist and have providers."""
        categories = get_providers_by_category()
        assert "Starter" in categories
        assert len(categories["Starter"]) > 0

    def test_provider_has_required_fields(self):
        """Each provider should have name, description, package."""
        categories = get_providers_by_category()
        for category, providers in categories.items():
            for provider in providers:
                assert provider.name
                assert provider.description
                assert provider.package


class TestConfigFileManager:
    """Tests for configuration file management."""

    def test_generate_initial_config_basic(self):
        """Generated config should be valid YAML structure."""
        manager = ConfigFileManager()
        providers = [get_provider("fetch"), get_provider("memory")]
        providers = [p for p in providers if p is not None]

        config_str = manager.generate_initial_config(providers=providers, configs={})

        assert "providers:" in config_str
        assert "fetch:" in config_str
        assert "memory:" in config_str
        assert "mode: subprocess" in config_str

    def test_generate_initial_config_with_path_config(self):
        """Config should include args for path-based providers."""
        manager = ConfigFileManager()
        provider = get_provider("filesystem")
        assert provider is not None

        config_str = manager.generate_initial_config(
            providers=[provider],
            configs={"filesystem": {"path": "/home/user/documents"}},
        )

        assert "/home/user/documents" in config_str
        assert "args:" in config_str

    def test_generate_initial_config_with_env_var(self):
        """Config should include env var references."""
        manager = ConfigFileManager()
        provider = get_provider("github")
        assert provider is not None

        config_str = manager.generate_initial_config(
            providers=[provider],
            configs={"github": {"use_env": "GITHUB_TOKEN"}},
        )

        assert "env:" in config_str
        assert "GITHUB_TOKEN" in config_str

    def test_generate_initial_config_includes_health_check(self):
        """Config should include health check settings."""
        manager = ConfigFileManager()
        provider = get_provider("fetch")
        assert provider is not None

        config_str = manager.generate_initial_config(providers=[provider], configs={})

        assert "health_check:" in config_str
        assert "enabled: true" in config_str

    def test_generate_initial_config_includes_logging(self):
        """Config should include logging settings."""
        manager = ConfigFileManager()
        provider = get_provider("fetch")
        assert provider is not None

        config_str = manager.generate_initial_config(providers=[provider], configs={})

        assert "logging:" in config_str
        assert "level: INFO" in config_str

    def test_backup_creates_timestamped_copy(self):
        """Backup should create a copy with timestamp."""
        with TemporaryDirectory() as tmpdir:
            original = Path(tmpdir) / "config.yaml"
            original.write_text("test content")
            manager = ConfigFileManager(original)

            backup_path = manager.backup()

            assert backup_path is not None
            assert backup_path.exists()
            assert ".backup." in backup_path.name
            assert backup_path.read_text() == "test content"

    def test_backup_returns_none_for_missing_file(self):
        """Backup should return None if file doesn't exist."""
        manager = ConfigFileManager(Path("/nonexistent/file.yaml"))
        result = manager.backup()
        assert result is None


class TestDefaultConfigPath:
    """Tests for default configuration path."""

    def test_default_config_path_in_home_config(self):
        """Default config should be in ~/.config/mcp-hangar/."""
        assert ".config" in str(ConfigFileManager.DEFAULT_CONFIG_PATH)
        assert "mcp-hangar" in str(ConfigFileManager.DEFAULT_CONFIG_PATH)
        assert ConfigFileManager.DEFAULT_CONFIG_PATH.name == "config.yaml"
