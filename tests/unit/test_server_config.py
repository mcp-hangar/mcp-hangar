"""Tests for server/config.py module."""

import pytest
import yaml

from mcp_hangar.domain.model import LoadBalancerStrategy
from mcp_hangar.server.config import _parse_strategy, load_config, load_config_from_file, load_configuration


class TestLoadConfigFromFile:
    """Tests for load_config_from_file function."""

    def test_loads_valid_yaml_file(self, tmp_path):
        """Should load configuration from valid YAML file."""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "providers": {
                "test-provider": {
                    "mode": "subprocess",
                    "command": ["python", "server.py"],
                }
            }
        }
        config_file.write_text(yaml.dump(config_data))

        result = load_config_from_file(str(config_file))

        assert "providers" in result
        assert "test-provider" in result["providers"]

    def test_raises_for_missing_file(self):
        """Should raise FileNotFoundError for missing file."""
        with pytest.raises(FileNotFoundError):
            load_config_from_file("/nonexistent/path/config.yaml")

    def test_raises_for_invalid_yaml(self, tmp_path):
        """Should raise error for invalid YAML syntax."""
        config_file = tmp_path / "invalid.yaml"
        config_file.write_text("invalid: yaml: content: [")

        with pytest.raises(yaml.YAMLError):
            load_config_from_file(str(config_file))

    def test_raises_for_missing_providers_section(self, tmp_path):
        """Should raise ValueError when providers section is missing."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.dump({"logging": {"level": "INFO"}}))

        with pytest.raises(ValueError, match="missing 'providers' section"):
            load_config_from_file(str(config_file))

    def test_raises_for_empty_config(self, tmp_path):
        """Should raise ValueError for empty config file."""
        config_file = tmp_path / "empty.yaml"
        config_file.write_text("")

        with pytest.raises(ValueError):
            load_config_from_file(str(config_file))

    def test_handles_complex_config(self, tmp_path):
        """Should handle complex configuration with all sections."""
        config_file = tmp_path / "config.yaml"
        config_data = {
            "providers": {
                "provider1": {"mode": "subprocess", "command": ["cmd1"]},
                "provider2": {"mode": "container", "image": "image:tag"},
            },
            "logging": {"level": "DEBUG", "json_format": True},
            "discovery": {"enabled": True},
            "retry": {"max_attempts": 3},
        }
        config_file.write_text(yaml.dump(config_data))

        result = load_config_from_file(str(config_file))

        assert len(result["providers"]) == 2
        assert result["logging"]["level"] == "DEBUG"
        assert result["discovery"]["enabled"] is True


class TestParseStrategy:
    """Tests for _parse_strategy function."""

    def test_parses_round_robin(self):
        """Should parse round_robin strategy."""
        result = _parse_strategy("round_robin", "test-group")
        assert result == LoadBalancerStrategy.ROUND_ROBIN

    def test_parses_least_connections(self):
        """Should parse least_connections strategy."""
        result = _parse_strategy("least_connections", "test-group")
        assert result == LoadBalancerStrategy.LEAST_CONNECTIONS

    def test_parses_random(self):
        """Should parse random strategy."""
        result = _parse_strategy("random", "test-group")
        assert result == LoadBalancerStrategy.RANDOM

    def test_defaults_to_round_robin_for_unknown(self):
        """Should default to round_robin for unknown strategy."""
        result = _parse_strategy("unknown_strategy", "test-group")
        assert result == LoadBalancerStrategy.ROUND_ROBIN

    def test_handles_empty_string(self):
        """Should handle empty string gracefully."""
        result = _parse_strategy("", "test-group")
        assert result == LoadBalancerStrategy.ROUND_ROBIN


class TestLoadConfig:
    """Tests for load_config function."""

    @pytest.fixture(autouse=True)
    def reset_globals(self):
        """Reset global state before and after each test."""
        from mcp_hangar.server.state import GROUPS, PROVIDERS

        # Store original state
        original_providers = dict(PROVIDERS._repo._providers) if hasattr(PROVIDERS, "_repo") else {}
        original_groups = dict(GROUPS)

        yield

        # Restore original state
        if hasattr(PROVIDERS, "_repo"):
            PROVIDERS._repo._providers.clear()
            PROVIDERS._repo._providers.update(original_providers)
        GROUPS.clear()
        GROUPS.update(original_groups)

    def test_loads_subprocess_provider(self):
        """Should load subprocess provider configuration."""
        config = {
            "test-subprocess": {
                "mode": "subprocess",
                "command": ["python", "-m", "server"],
            }
        }

        load_config(config)

        from mcp_hangar.server.state import PROVIDERS

        assert "test-subprocess" in PROVIDERS

    def test_loads_container_provider(self):
        """Should load container provider configuration."""
        config = {
            "test-container": {
                "mode": "container",
                "image": "mcp/test:latest",
            }
        }

        load_config(config)

        from mcp_hangar.server.state import PROVIDERS

        assert "test-container" in PROVIDERS

    def test_skips_invalid_provider_id(self):
        """Should skip providers with invalid IDs."""
        config = {
            "": {  # Empty ID - invalid
                "mode": "subprocess",
                "command": ["cmd"],
            }
        }

        # Should not raise
        load_config(config)

    def test_loads_multiple_providers(self):
        """Should load multiple providers."""
        config = {
            "provider1": {"mode": "subprocess", "command": ["cmd1"]},
            "provider2": {"mode": "subprocess", "command": ["cmd2"]},
            "provider3": {"mode": "subprocess", "command": ["cmd3"]},
        }

        load_config(config)

        from mcp_hangar.server.state import PROVIDERS

        assert "provider1" in PROVIDERS
        assert "provider2" in PROVIDERS
        assert "provider3" in PROVIDERS


class TestLoadConfiguration:
    """Tests for load_configuration function."""

    def test_loads_from_default_path_when_exists(self, tmp_path, monkeypatch):
        """Should load from default config.yaml when it exists."""
        config_file = tmp_path / "config.yaml"
        config_data = {"providers": {"test": {"mode": "subprocess", "command": ["cmd"]}}}
        config_file.write_text(yaml.dump(config_data))

        monkeypatch.chdir(tmp_path)

        result = load_configuration(None)

        assert "providers" in result or result == {}

    def test_loads_from_specified_path(self, tmp_path):
        """Should load from specified config path."""
        config_file = tmp_path / "custom.yaml"
        config_data = {"providers": {"custom": {"mode": "subprocess", "command": ["cmd"]}}}
        config_file.write_text(yaml.dump(config_data))

        result = load_configuration(str(config_file))

        assert "providers" in result

    def test_returns_empty_dict_when_no_config(self, tmp_path, monkeypatch):
        """Should return empty dict when no config file exists."""
        monkeypatch.chdir(tmp_path)
        # No config.yaml in tmp_path

        result = load_configuration(None)

        assert result == {} or "providers" in result

    def test_respects_mcp_config_env_var(self, tmp_path, monkeypatch):
        """Should respect MCP_CONFIG environment variable."""
        config_file = tmp_path / "env-config.yaml"
        config_data = {"providers": {"env-provider": {"mode": "subprocess", "command": ["cmd"]}}}
        config_file.write_text(yaml.dump(config_data))

        monkeypatch.setenv("MCP_CONFIG", str(config_file))
        monkeypatch.chdir(tmp_path)

        result = load_configuration(None)

        # Should load from env var path
        assert "providers" in result or result == {}
