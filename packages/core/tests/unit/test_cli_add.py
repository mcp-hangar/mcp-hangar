"""Tests for CLI add command.

Tests cover provider search, config updates, and installation logic.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_hangar.server.cli.commands.add import (
    _get_provider_info,
    _search_registry,
    _update_config_file,
    KNOWN_PROVIDERS,
)


class TestKnownProviders:
    """Tests for known provider definitions."""

    def test_known_providers_has_common_providers(self):
        """Known providers should include common ones."""
        assert "filesystem" in KNOWN_PROVIDERS
        assert "fetch" in KNOWN_PROVIDERS
        assert "memory" in KNOWN_PROVIDERS
        assert "github" in KNOWN_PROVIDERS
        assert "sqlite" in KNOWN_PROVIDERS

    def test_provider_has_required_fields(self):
        """Each known provider should have essential fields."""
        for name, info in KNOWN_PROVIDERS.items():
            assert "description" in info
            assert "package" in info
            assert "install_type" in info


class TestSearchRegistry:
    """Tests for registry search functionality."""

    def test_search_exact_match(self):
        """Searching for exact name should return provider."""
        results = _search_registry("filesystem")
        assert len(results) > 0
        assert any(r["name"] == "filesystem" for r in results)

    def test_search_partial_match(self):
        """Searching should match partial names."""
        results = _search_registry("file")
        assert any(r["name"] == "filesystem" for r in results)

    def test_search_by_description(self):
        """Searching should match description text."""
        results = _search_registry("database")
        assert len(results) > 0

    def test_search_no_results(self):
        """Searching for nonexistent returns empty list."""
        results = _search_registry("xyznonexistent")
        assert len(results) == 0


class TestGetProviderInfo:
    """Tests for provider info retrieval."""

    def test_get_known_provider(self):
        """Should return info for known providers."""
        info = _get_provider_info("filesystem")
        assert info is not None
        assert info["name"] == "filesystem"

    def test_get_unknown_provider(self):
        """Should return None for unknown providers."""
        info = _get_provider_info("nonexistent_provider_xyz")
        assert info is None


class TestUpdateConfigFile:
    """Tests for config file updates."""

    def test_update_creates_new_config(self):
        """Should create config file if it doesn't exist."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            _update_config_file(
                config_path,
                "fetch",
                KNOWN_PROVIDERS["fetch"],
                {},
            )

            assert config_path.exists()
            content = config_path.read_text()
            assert "fetch:" in content
            assert "providers:" in content

    def test_update_adds_to_existing_config(self):
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

            _update_config_file(
                config_path,
                "fetch",
                KNOWN_PROVIDERS["fetch"],
                {},
            )

            content = config_path.read_text()
            assert "fetch:" in content
            assert "memory:" in content  # Original preserved

    def test_update_includes_command(self):
        """Should include command from provider info."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            _update_config_file(
                config_path,
                "fetch",
                KNOWN_PROVIDERS["fetch"],
                {},
            )

            content = config_path.read_text()
            assert "@anthropic/mcp-server-fetch" in content

    def test_update_includes_path_config(self):
        """Should include path in args for path-based providers."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            _update_config_file(
                config_path,
                "filesystem",
                KNOWN_PROVIDERS["filesystem"],
                {"value": "/home/user", "config_type": "path"},
            )

            content = config_path.read_text()
            assert "/home/user" in content

    def test_update_includes_env_var(self):
        """Should include env var configuration."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"

            _update_config_file(
                config_path,
                "github",
                KNOWN_PROVIDERS["github"],
                {"use_env": "GITHUB_TOKEN"},
            )

            content = config_path.read_text()
            assert "GITHUB_TOKEN" in content
            assert "env:" in content
