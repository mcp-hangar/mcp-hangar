"""Tests for ReloadConfigurationHandler."""

import os
import tempfile
from unittest.mock import Mock, patch

import pytest
import yaml

from mcp_hangar.application.commands.reload_handler import ReloadConfigurationHandler
from mcp_hangar.application.commands import ReloadConfigurationCommand
from mcp_hangar.domain.events import ConfigurationReloaded, ConfigurationReloadFailed, ConfigurationReloadRequested
from mcp_hangar.domain.exceptions import ConfigurationError
from mcp_hangar.domain.model import Provider


class TestReloadConfigurationHandler:
    """Tests for ReloadConfigurationHandler."""

    @pytest.fixture
    def mock_repository(self):
        """Create mock provider repository."""
        repo = Mock()
        repo.get_all.return_value = {}
        repo.get.return_value = None
        repo.remove.return_value = None
        repo.add.return_value = None
        return repo

    @pytest.fixture
    def mock_event_bus(self):
        """Create mock event bus."""
        bus = Mock()
        bus.publish.return_value = None
        return bus

    @pytest.fixture
    def handler(self, mock_repository, mock_event_bus):
        """Create handler instance."""
        return ReloadConfigurationHandler(mock_repository, mock_event_bus)

    @pytest.fixture
    def temp_config_file(self):
        """Create temporary config file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "providers": {
                    "test-provider": {
                        "mode": "subprocess",
                        "command": ["python", "-m", "test_server"],
                        "idle_ttl_s": 300,
                    }
                }
            }
            yaml.dump(config, f)
            config_path = f.name

        yield config_path

        # Cleanup
        if os.path.exists(config_path):
            os.unlink(config_path)

    def test_reload_with_no_config_path_raises_error(self, handler):
        """Should raise ConfigurationError when no config path provided."""
        command = ReloadConfigurationCommand()

        with pytest.raises(ConfigurationError, match="No configuration path specified"):
            handler.handle(command)

    def test_reload_publishes_requested_event(self, handler, mock_event_bus, temp_config_file):
        """Should publish ConfigurationReloadRequested event."""
        command = ReloadConfigurationCommand(
            config_path=temp_config_file,
            requested_by="test",
        )

        with patch("mcp_hangar.application.commands.reload_handler.load_config"):
            handler.handle(command)

        # Check first event published
        first_call = mock_event_bus.publish.call_args_list[0]
        event = first_call[0][0]
        assert isinstance(event, ConfigurationReloadRequested)
        assert event.config_path == temp_config_file
        assert event.requested_by == "test"

    def test_reload_with_invalid_config_publishes_failed_event(self, handler, mock_event_bus):
        """Should publish ConfigurationReloadFailed on invalid config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            # Invalid YAML - missing providers section
            yaml.dump({"logging": {"level": "INFO"}}, f)
            invalid_config = f.name

        try:
            command = ReloadConfigurationCommand(config_path=invalid_config)

            with pytest.raises(ConfigurationError):
                handler.handle(command)

            # Check failed event published
            failed_events = [
                call[0][0]
                for call in mock_event_bus.publish.call_args_list
                if isinstance(call[0][0], ConfigurationReloadFailed)
            ]
            assert len(failed_events) == 1
            assert failed_events[0].config_path == invalid_config

        finally:
            os.unlink(invalid_config)

    def test_reload_detects_added_providers(self, handler, mock_repository, mock_event_bus, temp_config_file):
        """Should detect and add new providers."""
        # No existing providers
        mock_repository.get_all.return_value = {}

        command = ReloadConfigurationCommand(
            config_path=temp_config_file,
            graceful=True,
        )

        with patch("mcp_hangar.application.commands.reload_handler.load_config"):
            result = handler.handle(command)

        assert result["success"] is True
        assert "test-provider" in result["providers_added"]
        assert len(result["providers_removed"]) == 0
        assert len(result["providers_updated"]) == 0

        # Check success event published
        success_events = [
            call[0][0]
            for call in mock_event_bus.publish.call_args_list
            if isinstance(call[0][0], ConfigurationReloaded)
        ]
        assert len(success_events) == 1
        assert "test-provider" in success_events[0].providers_added

    def test_reload_detects_removed_providers(self, handler, mock_repository, mock_event_bus):
        """Should detect and remove deleted providers."""
        # Create existing provider
        existing_provider = Mock(spec=Provider)
        existing_provider._mode = Mock(value="subprocess")
        existing_provider._command = ["old", "command"]
        existing_provider._image = None
        existing_provider._endpoint = None
        existing_provider._env = {}
        existing_provider._idle_ttl = Mock(seconds=300)
        existing_provider._health_check_interval = Mock(seconds=60)
        existing_provider._health = Mock(max_consecutive_failures=3)
        existing_provider._volumes = []
        existing_provider._build = None
        existing_provider._resources = {}
        existing_provider._network = "none"
        existing_provider._read_only = True
        existing_provider._user = None
        existing_provider._description = None
        existing_provider._tools = Mock(to_dict=lambda: None)
        existing_provider.stop.return_value = None

        mock_repository.get_all.return_value = {"old-provider": existing_provider}
        mock_repository.get.return_value = existing_provider

        # Create config without old-provider
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {"providers": {}}
            yaml.dump(config, f)
            config_path = f.name

        try:
            command = ReloadConfigurationCommand(
                config_path=config_path,
                graceful=True,
            )

            with patch("mcp_hangar.application.commands.reload_handler.load_config"):
                result = handler.handle(command)

            assert result["success"] is True
            assert "old-provider" in result["providers_removed"]
            existing_provider.stop.assert_called_once_with(reason="config_reload")
            mock_repository.remove.assert_called_once_with("old-provider")

        finally:
            os.unlink(config_path)

    def test_reload_detects_updated_providers(self, handler, mock_repository, mock_event_bus):
        """Should detect providers with changed configuration."""
        # Create existing provider with old config
        existing_provider = Mock(spec=Provider)
        existing_provider._mode = Mock(value="subprocess")
        existing_provider._command = ["old", "command"]
        existing_provider._image = None
        existing_provider._endpoint = None
        existing_provider._env = {"OLD_ENV": "value"}
        existing_provider._idle_ttl = Mock(seconds=300)
        existing_provider._health_check_interval = Mock(seconds=60)
        existing_provider._health = Mock(max_consecutive_failures=3)
        existing_provider._volumes = []
        existing_provider._build = None
        existing_provider._resources = {}
        existing_provider._network = "none"
        existing_provider._read_only = True
        existing_provider._user = None
        existing_provider._description = None
        existing_provider._tools = Mock(to_dict=lambda: None)
        existing_provider.stop.return_value = None

        mock_repository.get_all.return_value = {"test-provider": existing_provider}
        mock_repository.get.return_value = existing_provider

        # Create config with modified provider
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "providers": {
                    "test-provider": {
                        "mode": "subprocess",
                        "command": ["new", "command"],  # Changed
                        "idle_ttl_s": 300,
                    }
                }
            }
            yaml.dump(config, f)
            config_path = f.name

        try:
            command = ReloadConfigurationCommand(
                config_path=config_path,
                graceful=True,
            )

            with patch("mcp_hangar.application.commands.reload_handler.load_config"):
                result = handler.handle(command)

            assert result["success"] is True
            assert "test-provider" in result["providers_updated"]
            existing_provider.stop.assert_called_once_with(reason="config_reload")

        finally:
            os.unlink(config_path)

    def test_reload_preserves_unchanged_providers(self, handler, mock_repository, mock_event_bus):
        """Should not restart providers with unchanged configuration."""
        # Create existing provider
        existing_provider = Mock(spec=Provider)
        existing_provider._mode = Mock(value="subprocess")
        existing_provider._command = ["python", "-m", "test_server"]
        existing_provider._image = None
        existing_provider._endpoint = None
        existing_provider._env = {}
        existing_provider._idle_ttl = Mock(seconds=300)
        existing_provider._health_check_interval = Mock(seconds=60)
        existing_provider._health = Mock(max_consecutive_failures=3)
        existing_provider._volumes = []
        existing_provider._build = None
        existing_provider._resources = {}
        existing_provider._network = "none"
        existing_provider._read_only = True
        existing_provider._user = None
        existing_provider._description = None
        existing_provider._tools = Mock(to_dict=lambda: None)

        mock_repository.get_all.return_value = {"test-provider": existing_provider}
        mock_repository.get.return_value = existing_provider

        # Create config with same provider config
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {
                "providers": {
                    "test-provider": {
                        "mode": "subprocess",
                        "command": ["python", "-m", "test_server"],
                        "idle_ttl_s": 300,
                    }
                }
            }
            yaml.dump(config, f)
            config_path = f.name

        try:
            command = ReloadConfigurationCommand(
                config_path=config_path,
                graceful=True,
            )

            with patch("mcp_hangar.application.commands.reload_handler.load_config"):
                result = handler.handle(command)

            assert result["success"] is True
            assert "test-provider" in result["providers_unchanged"]
            existing_provider.stop.assert_not_called()

        finally:
            os.unlink(config_path)

    def test_reload_graceful_vs_immediate(self, handler, mock_repository, mock_event_bus):
        """Should respect graceful flag when stopping providers."""
        # Create existing provider
        existing_provider = Mock(spec=Provider)
        existing_provider._mode = Mock(value="subprocess")
        existing_provider._command = ["old", "command"]
        existing_provider._image = None
        existing_provider._endpoint = None
        existing_provider._env = {}
        existing_provider._idle_ttl = Mock(seconds=300)
        existing_provider._health_check_interval = Mock(seconds=60)
        existing_provider._health = Mock(max_consecutive_failures=3)
        existing_provider._volumes = []
        existing_provider._build = None
        existing_provider._resources = {}
        existing_provider._network = "none"
        existing_provider._read_only = True
        existing_provider._user = None
        existing_provider._description = None
        existing_provider._tools = Mock(to_dict=lambda: None)
        existing_provider.stop.return_value = None
        existing_provider.shutdown.return_value = None

        mock_repository.get_all.return_value = {"test-provider": existing_provider}
        mock_repository.get.return_value = existing_provider

        # Create empty config to remove provider
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            config = {"providers": {}}
            yaml.dump(config, f)
            config_path = f.name

        try:
            # Test graceful=False
            command = ReloadConfigurationCommand(
                config_path=config_path,
                graceful=False,
            )

            with patch("mcp_hangar.application.commands.reload_handler.load_config"):
                handler.handle(command)

            existing_provider.shutdown.assert_called_once()
            existing_provider.stop.assert_not_called()

        finally:
            os.unlink(config_path)
