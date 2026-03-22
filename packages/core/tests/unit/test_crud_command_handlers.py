"""Unit tests for Provider CRUD command handlers.

Tests cover:
- ProviderRegistered, ProviderUpdated, ProviderDeregistered domain events
- GroupUpdated, GroupDeleted group events
- Provider.to_config_dict() serialization
- CreateProviderHandler: creates provider, emits event, rejects duplicates
- UpdateProviderHandler: updates mutable fields, emits event, raises on missing
- DeleteProviderHandler: removes provider, shuts down if running, emits event
"""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.events import ProviderDeregistered, ProviderRegistered, ProviderUpdated
from mcp_hangar.domain.exceptions import ProviderNotFoundError, ValidationError
from mcp_hangar.domain.model.provider import Provider
from mcp_hangar.domain.model.provider_group import GroupDeleted, GroupUpdated
from mcp_hangar.domain.repository import InMemoryProviderRepository
from mcp_hangar.domain.value_objects import ProviderState
from mcp_hangar.application.commands.crud_commands import (
    CreateProviderCommand,
    DeleteProviderCommand,
    UpdateProviderCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateProviderHandler,
    DeleteProviderHandler,
    UpdateProviderHandler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cold_provider(provider_id: str = "p", mode: str = "subprocess") -> Provider:
    """Create a minimal COLD provider for testing."""
    return Provider(provider_id=provider_id, mode=mode, command=["python", "-m", "test"])


def _make_event_bus() -> MagicMock:
    """Create a mock EventBus with a publish() method."""
    bus = MagicMock()
    bus.publish = MagicMock()
    return bus


# ---------------------------------------------------------------------------
# TestProviderEvents
# ---------------------------------------------------------------------------


class TestProviderEvents:
    """Tests that the new domain event dataclasses are constructible and correct."""

    def test_provider_registered_event_has_source_field(self):
        event = ProviderRegistered(provider_id="x", source="api", mode="subprocess")
        assert event.source == "api"
        assert event.provider_id == "x"
        assert event.mode == "subprocess"

    def test_provider_updated_event_has_source_field(self):
        event = ProviderUpdated(provider_id="x", source="api")
        assert event.source == "api"
        assert event.provider_id == "x"

    def test_provider_deregistered_event_has_source_field(self):
        event = ProviderDeregistered(provider_id="x", source="api")
        assert event.source == "api"
        assert event.provider_id == "x"

    def test_group_updated_event(self):
        event = GroupUpdated(group_id="g")
        assert event.group_id == "g"

    def test_group_deleted_event(self):
        event = GroupDeleted(group_id="g")
        assert event.group_id == "g"


# ---------------------------------------------------------------------------
# TestProviderToConfigDict
# ---------------------------------------------------------------------------


class TestProviderToConfigDict:
    """Tests for Provider.to_config_dict() round-trip serialization."""

    def test_subprocess_provider_config_dict(self):
        provider = Provider(
            provider_id="p",
            mode="subprocess",
            command=["python", "-m", "test"],
        )
        cfg = provider.to_config_dict()
        assert cfg["mode"] == "subprocess"
        assert cfg["command"] == ["python", "-m", "test"]
        assert cfg["idle_ttl_s"] == 300
        assert cfg["health_check_interval_s"] == 60

    def test_docker_provider_config_dict(self):
        provider = Provider(provider_id="p", mode="docker", image="myimage")
        cfg = provider.to_config_dict()
        assert cfg["mode"] == "docker"
        assert cfg["image"] == "myimage"

    def test_description_included_when_set(self):
        provider = Provider(provider_id="p", mode="subprocess", command=["x"], description="My desc")
        assert provider.to_config_dict()["description"] == "My desc"

    def test_env_included_when_set(self):
        provider = Provider(provider_id="p", mode="subprocess", command=["x"], env={"FOO": "bar"})
        assert provider.to_config_dict()["env"] == {"FOO": "bar"}

    def test_empty_env_omitted(self):
        provider = Provider(provider_id="p", mode="subprocess", command=["x"])
        cfg = provider.to_config_dict()
        assert "env" not in cfg


# ---------------------------------------------------------------------------
# TestCreateProviderHandler
# ---------------------------------------------------------------------------


class TestCreateProviderHandler:
    """Tests for CreateProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryProviderRepository()
        self.event_bus = _make_event_bus()
        self.handler = CreateProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_creates_provider_in_repository(self):
        cmd = CreateProviderCommand(provider_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        assert self.repo.exists("p")

    def test_emits_provider_registered_event(self):
        cmd = CreateProviderCommand(provider_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ProviderRegistered)
        assert published_event.provider_id == "p"
        assert published_event.source == "api"
        assert published_event.mode == "subprocess"

    def test_duplicate_raises_validation_error(self):
        cmd = CreateProviderCommand(provider_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        with pytest.raises(ValidationError):
            self.handler.handle(cmd)

    def test_source_field_propagated(self):
        cmd = CreateProviderCommand(provider_id="p", mode="subprocess", command=["x"], source="config")
        self.handler.handle(cmd)
        published_event = self.event_bus.publish.call_args[0][0]
        assert published_event.source == "config"


# ---------------------------------------------------------------------------
# TestUpdateProviderHandler
# ---------------------------------------------------------------------------


class TestUpdateProviderHandler:
    """Tests for UpdateProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryProviderRepository()
        self.event_bus = _make_event_bus()
        # Pre-populate repo with a COLD provider
        provider = _make_cold_provider(provider_id="p")
        self.repo.add("p", provider)
        self.handler = UpdateProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_updates_description(self):
        cmd = UpdateProviderCommand(provider_id="p", description="new")
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["description"] == "new"

    def test_updates_env(self):
        cmd = UpdateProviderCommand(provider_id="p", env={"K": "V"})
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["env"] == {"K": "V"}

    def test_updates_idle_ttl(self):
        cmd = UpdateProviderCommand(provider_id="p", idle_ttl_s=600)
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["idle_ttl_s"] == 600

    def test_emits_provider_updated_event(self):
        cmd = UpdateProviderCommand(provider_id="p", description="updated")
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ProviderUpdated)
        assert published_event.provider_id == "p"
        assert published_event.source == "api"

    def test_missing_provider_raises_not_found(self):
        cmd = UpdateProviderCommand(provider_id="nonexistent", description="x")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)


# ---------------------------------------------------------------------------
# TestDeleteProviderHandler
# ---------------------------------------------------------------------------


class TestDeleteProviderHandler:
    """Tests for DeleteProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryProviderRepository()
        self.event_bus = _make_event_bus()
        self.handler = DeleteProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_removes_from_repository(self):
        provider = _make_cold_provider(provider_id="p")
        self.repo.add("p", provider)
        cmd = DeleteProviderCommand(provider_id="p")
        self.handler.handle(cmd)
        assert not self.repo.exists("p")

    def test_emits_provider_deregistered_event(self):
        provider = _make_cold_provider(provider_id="p")
        self.repo.add("p", provider)
        cmd = DeleteProviderCommand(provider_id="p")
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, ProviderDeregistered)
        assert published_event.provider_id == "p"
        assert published_event.source == "api"

    def test_stops_ready_provider_before_delete(self):
        provider = MagicMock(spec=Provider)
        provider.state = ProviderState.READY
        provider.id = "p"
        self.repo.add("p", provider)
        cmd = DeleteProviderCommand(provider_id="p")
        self.handler.handle(cmd)
        provider.shutdown.assert_called_once()

    def test_cold_provider_not_stopped(self):
        provider = MagicMock(spec=Provider)
        provider.state = ProviderState.COLD
        provider.id = "p"
        self.repo.add("p", provider)
        cmd = DeleteProviderCommand(provider_id="p")
        self.handler.handle(cmd)
        provider.shutdown.assert_not_called()

    def test_missing_provider_raises_not_found(self):
        cmd = DeleteProviderCommand(provider_id="nonexistent")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)
