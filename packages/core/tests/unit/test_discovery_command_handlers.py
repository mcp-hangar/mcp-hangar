"""Unit tests for discovery source CQRS command handlers.

Tests cover happy paths and error cases for all 5 handlers:
RegisterDiscoverySourceHandler, UpdateDiscoverySourceHandler,
DeregisterDiscoverySourceHandler, TriggerSourceScanHandler,
ToggleDiscoverySourceHandler.
"""

from unittest.mock import Mock

import pytest

from mcp_hangar.application.commands.discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
    TriggerSourceScanCommand,
    UpdateDiscoverySourceCommand,
)
from mcp_hangar.application.commands.discovery_handlers import (
    DeregisterDiscoverySourceHandler,
    RegisterDiscoverySourceHandler,
    ToggleDiscoverySourceHandler,
    TriggerSourceScanHandler,
    UpdateDiscoverySourceHandler,
)
from mcp_hangar.domain.exceptions import ProviderNotFoundError


class TestRegisterDiscoverySourceHandler:
    """Tests for RegisterDiscoverySourceHandler."""

    def test_handle_registers_spec_and_returns_source_id(self):
        """Handler registers spec and returns source_id + registered=True."""
        registry = Mock()
        handler = RegisterDiscoverySourceHandler(registry=registry)
        result = handler.handle(RegisterDiscoverySourceCommand(source_type="docker", mode="additive"))
        assert result["registered"] is True
        assert isinstance(result["source_id"], str)
        assert len(result["source_id"]) > 0

    def test_handle_generates_unique_uuid_per_call(self):
        """Handler generates a unique non-empty UUID for source_id on each call."""
        registry = Mock()
        handler = RegisterDiscoverySourceHandler(registry=registry)
        r1 = handler.handle(RegisterDiscoverySourceCommand(source_type="docker", mode="additive"))
        r2 = handler.handle(RegisterDiscoverySourceCommand(source_type="docker", mode="additive"))
        # UUIDs are unique across calls
        assert r1["source_id"] != r2["source_id"]

    def test_handle_calls_registry_register_source_once(self):
        """Handler calls registry.register_source exactly once per command."""
        registry = Mock()
        handler = RegisterDiscoverySourceHandler(registry=registry)
        handler.handle(RegisterDiscoverySourceCommand(source_type="filesystem", mode="authoritative"))
        registry.register_source.assert_called_once()

    def test_handle_passes_enabled_and_config_to_spec(self):
        """Handler passes enabled and config from command to DiscoverySourceSpec."""
        registry = Mock()
        handler = RegisterDiscoverySourceHandler(registry=registry)
        handler.handle(
            RegisterDiscoverySourceCommand(
                source_type="docker",
                mode="additive",
                enabled=False,
                config={"socket_path": "/var/run/docker.sock"},
            )
        )
        spec = registry.register_source.call_args[0][0]
        assert spec.enabled is False
        assert spec.config == {"socket_path": "/var/run/docker.sock"}


class TestUpdateDiscoverySourceHandler:
    """Tests for UpdateDiscoverySourceHandler."""

    def test_handle_returns_source_id_and_updated_true(self):
        """Handler returns source_id + updated=True on success."""
        registry = Mock()
        handler = UpdateDiscoverySourceHandler(registry=registry)
        result = handler.handle(UpdateDiscoverySourceCommand(source_id="uid-1", enabled=False))
        assert result["updated"] is True
        assert result["source_id"] == "uid-1"

    def test_handle_passes_only_non_none_fields(self):
        """Handler only passes non-None fields to registry.update_source."""
        registry = Mock()
        handler = UpdateDiscoverySourceHandler(registry=registry)
        handler.handle(UpdateDiscoverySourceCommand(source_id="uid-1", enabled=True))
        call_kwargs = registry.update_source.call_args[1]
        assert "enabled" in call_kwargs
        assert "mode" not in call_kwargs
        assert "config" not in call_kwargs

    def test_handle_raises_provider_not_found_on_key_error(self):
        """KeyError from registry.update_source is re-raised as ProviderNotFoundError."""
        registry = Mock()
        registry.update_source.side_effect = KeyError("not found")
        handler = UpdateDiscoverySourceHandler(registry=registry)
        with pytest.raises(ProviderNotFoundError):
            handler.handle(UpdateDiscoverySourceCommand(source_id="missing"))


class TestDeregisterDiscoverySourceHandler:
    """Tests for DeregisterDiscoverySourceHandler."""

    def test_handle_calls_unregister_source_with_correct_id(self):
        """Handler calls registry.unregister_source with the correct source_id."""
        registry = Mock()
        handler = DeregisterDiscoverySourceHandler(registry=registry)
        result = handler.handle(DeregisterDiscoverySourceCommand(source_id="uid-1"))
        registry.unregister_source.assert_called_once_with("uid-1")
        assert result["deregistered"] is True

    def test_handle_raises_provider_not_found_on_key_error(self):
        """KeyError from registry.unregister_source raises ProviderNotFoundError (not KeyError)."""
        registry = Mock()
        registry.unregister_source.side_effect = KeyError("not found")
        handler = DeregisterDiscoverySourceHandler(registry=registry)
        with pytest.raises(ProviderNotFoundError):
            handler.handle(DeregisterDiscoverySourceCommand(source_id="missing"))

    def test_handle_does_not_raise_raw_key_error(self):
        """ProviderNotFoundError (not raw KeyError) is raised for missing sources."""
        registry = Mock()
        registry.unregister_source.side_effect = KeyError("uid-404")
        handler = DeregisterDiscoverySourceHandler(registry=registry)
        try:
            handler.handle(DeregisterDiscoverySourceCommand(source_id="uid-404"))
        except ProviderNotFoundError:
            pass
        except KeyError:
            pytest.fail("Should not raise raw KeyError -- must raise ProviderNotFoundError")


class TestTriggerSourceScanHandler:
    """Tests for TriggerSourceScanHandler."""

    def test_handle_triggers_scan_and_returns_providers_found(self):
        """Handler calls orchestrator.trigger_discovery and returns providers_found count."""
        spec = Mock()
        registry = Mock()
        registry.get_source.return_value = spec
        registry.orchestrator.trigger_discovery.return_value = {"providers_discovered": 3, "cycle_id": "c1"}
        handler = TriggerSourceScanHandler(registry=registry)
        result = handler.handle(TriggerSourceScanCommand(source_id="uid-1"))
        assert result["scan_triggered"] is True
        assert result["providers_found"] == 3

    def test_handle_raises_provider_not_found_when_source_missing(self):
        """ProviderNotFoundError raised when get_source() returns None."""
        registry = Mock()
        registry.get_source.return_value = None
        handler = TriggerSourceScanHandler(registry=registry)
        with pytest.raises(ProviderNotFoundError):
            handler.handle(TriggerSourceScanCommand(source_id="missing"))

    def test_handle_providers_found_defaults_to_zero_on_missing_key(self):
        """providers_found defaults to 0 if result dict has no providers_discovered key."""
        registry = Mock()
        registry.get_source.return_value = Mock()
        registry.orchestrator.trigger_discovery.return_value = {"cycle_id": "c1"}
        handler = TriggerSourceScanHandler(registry=registry)
        result = handler.handle(TriggerSourceScanCommand(source_id="uid-1"))
        assert result["providers_found"] == 0


class TestToggleDiscoverySourceHandler:
    """Tests for ToggleDiscoverySourceHandler."""

    def test_handle_enable_calls_enable_source(self):
        """enabled=True in command calls registry.enable_source."""
        spec = Mock()
        spec.enabled = True
        registry = Mock()
        registry.enable_source.return_value = spec
        handler = ToggleDiscoverySourceHandler(registry=registry)
        result = handler.handle(ToggleDiscoverySourceCommand(source_id="uid-1", enabled=True))
        registry.enable_source.assert_called_once_with("uid-1")
        registry.disable_source.assert_not_called()
        assert result["enabled"] is True

    def test_handle_disable_calls_disable_source(self):
        """enabled=False in command calls registry.disable_source."""
        spec = Mock()
        spec.enabled = False
        registry = Mock()
        registry.disable_source.return_value = spec
        handler = ToggleDiscoverySourceHandler(registry=registry)
        result = handler.handle(ToggleDiscoverySourceCommand(source_id="uid-1", enabled=False))
        registry.disable_source.assert_called_once_with("uid-1")
        assert result["enabled"] is False

    def test_handle_raises_provider_not_found_on_key_error(self):
        """KeyError from enable_source raises ProviderNotFoundError."""
        registry = Mock()
        registry.enable_source.side_effect = KeyError("not found")
        handler = ToggleDiscoverySourceHandler(registry=registry)
        with pytest.raises(ProviderNotFoundError):
            handler.handle(ToggleDiscoverySourceCommand(source_id="missing", enabled=True))
