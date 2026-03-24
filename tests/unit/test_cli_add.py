"""Tests for CLI add command.

Tests cover provider search, config updates, and installation logic.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_hangar.server.cli.services import ConfigFileManager, get_all_providers, get_provider, search_providers


class TestProviderRegistry:
    """Tests for provider registry functionality."""

    def test_has_common_providers(self):
        """Registry should include common providers."""
        providers = {p.name for p in get_all_providers()}
        assert "filesystem" in providers
        assert "fetch" in providers
        assert "memory" in providers
        assert "github" in providers
        assert "sqlite" in providers

    def test_provider_has_required_fields(self):
        """Each provider should have essential fields."""
        for provider in get_all_providers():
            assert provider.name
            assert provider.description
            assert provider.package
            assert provider.install_type


class TestSearchRegistry:
    """Tests for registry search functionality."""

    def test_search_exact_match(self):
        """Searching for exact name should return provider."""
        results = search_providers("filesystem")
        assert len(results) > 0
        assert any(r.name == "filesystem" for r in results)

    def test_search_partial_match(self):
        """Searching should match partial names."""
        results = search_providers("file")
        assert any(r.name == "filesystem" for r in results)

    def test_search_by_description(self):
        """Searching should match description text."""
        results = search_providers("database")
        assert len(results) > 0

    def test_search_no_results(self):
        """Searching for nonexistent returns empty list."""
        results = search_providers("xyznonexistent")
        assert len(results) == 0


class TestGetProvider:
    """Tests for provider info retrieval."""

    def test_get_known_provider(self):
        """Should return info for known providers."""
        provider = get_provider("filesystem")
        assert provider is not None
        assert provider.name == "filesystem"

    def test_get_unknown_provider(self):
        """Should return None for unknown providers."""
        provider = get_provider("nonexistent_provider_xyz")
        assert provider is None


class TestConfigFileManager:
    """Tests for config file updates."""

    def test_add_provider_creates_new_config(self):
        """Should create config file if it doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            manager = ConfigFileManager(config_path)

            provider = get_provider("fetch")
            assert provider is not None
            manager.add_provider(provider)

            assert config_path.exists()
            content = config_path.read_text()
            assert "fetch:" in content
            assert "providers:" in content

    def test_add_provider_adds_to_existing_config(self):
        """Should add provider to existing config."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
providers:
  memory:
    mode: subprocess
    command: [npx, -y, "@anthropic/mcp-server-memory"]
"""
            )
            manager = ConfigFileManager(config_path)

            provider = get_provider("fetch")
            assert provider is not None
            manager.add_provider(provider)

            content = config_path.read_text()
            assert "fetch:" in content
            assert "memory:" in content  # Original preserved

    def test_add_provider_includes_command(self):
        """Should include command from provider info."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            manager = ConfigFileManager(config_path)

            provider = get_provider("fetch")
            assert provider is not None
            manager.add_provider(provider)

            content = config_path.read_text()
            # Should contain either uvx package name or npx package name
            assert "mcp-server-fetch" in content or "@modelcontextprotocol/server-fetch" in content

    def test_add_provider_includes_path_config(self):
        """Should include path in args for path-based providers."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            manager = ConfigFileManager(config_path)

            provider = get_provider("filesystem")
            assert provider is not None
            manager.add_provider(provider, config_value="/home/user")

            content = config_path.read_text()
            assert "/home/user" in content

    def test_add_provider_includes_env_var(self):
        """Should include env var configuration."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            manager = ConfigFileManager(config_path)

            provider = get_provider("github")
            assert provider is not None
            manager.add_provider(provider, use_env="GITHUB_TOKEN")

            content = config_path.read_text()
            assert "GITHUB_TOKEN" in content
            assert "env:" in content
