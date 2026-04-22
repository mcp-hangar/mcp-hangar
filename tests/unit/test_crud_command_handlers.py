"""Unit tests for Provider and Group CRUD command handlers.

Tests cover:
- McpServerRegistered, McpServerUpdated, McpServerDeregistered domain events
- GroupUpdated, GroupDeleted group events
- Provider.to_config_dict() serialization
- CreateProviderHandler: creates provider, emits event, rejects duplicates
- UpdateProviderHandler: updates mutable fields, emits event, raises on missing
- DeleteProviderHandler: removes provider, shuts down if running, emits event
- CreateGroupHandler: creates group, emits GroupCreated, rejects duplicates
- UpdateGroupHandler: updates group description/min_healthy, emits GroupUpdated
- DeleteGroupHandler: removes group, calls stop_all(), emits GroupDeleted
- AddGroupMemberHandler: adds provider to group, raises on missing provider/group
- RemoveGroupMemberHandler: removes provider from group, raises on missing group
"""

from unittest.mock import MagicMock

import pytest

from mcp_hangar.domain.events import McpServerDeregistered, McpServerRegistered, McpServerUpdated
from mcp_hangar.domain.exceptions import ProviderNotFoundError, ValidationError
from mcp_hangar.domain.model.provider import McpServer
from mcp_hangar.domain.model.mcp_server_group import (
    GroupCreated,
    GroupDeleted,
    GroupUpdated,
    McpServerGroup,
)
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects import ProviderState
from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateMcpServerCommand,
    DeleteGroupCommand,
    DeleteMcpServerCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    AddGroupMemberHandler,
    CreateGroupHandler,
    CreateProviderHandler,
    DeleteGroupHandler,
    DeleteProviderHandler,
    RemoveGroupMemberHandler,
    UpdateGroupHandler,
    UpdateProviderHandler,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_cold_provider(mcp_server_id: str = "p", mode: str = "subprocess") -> McpServer:
    """Create a minimal COLD provider for testing."""
    return McpServer(mcp_server_id=mcp_server_id, mode=mode, command=["python", "-m", "test"])


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
        event = McpServerRegistered(mcp_server_id="x", source="api", mode="subprocess")
        assert event.source == "api"
        assert event.mcp_server_id == "x"
        assert event.mode == "subprocess"

    def test_provider_updated_event_has_source_field(self):
        event = McpServerUpdated(mcp_server_id="x", source="api")
        assert event.source == "api"
        assert event.mcp_server_id == "x"

    def test_provider_deregistered_event_has_source_field(self):
        event = McpServerDeregistered(mcp_server_id="x", source="api")
        assert event.source == "api"
        assert event.mcp_server_id == "x"

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
        provider = McpServer(mcp_server_id="p", mode="subprocess",
        command=["python", "-m", "test"],)
        cfg = provider.to_config_dict()
        assert cfg["mode"] == "subprocess"
        assert cfg["command"] == ["python", "-m", "test"]
        assert cfg["idle_ttl_s"] == 300
        assert cfg["health_check_interval_s"] == 60

    def test_docker_provider_config_dict(self):
        provider = McpServer(mcp_server_id="p", mode="docker", image="myimage")
        cfg = provider.to_config_dict()
        assert cfg["mode"] == "docker"
        assert cfg["image"] == "myimage"

    def test_description_included_when_set(self):
        provider = McpServer(mcp_server_id="p", mode="subprocess", command=["x"], description="My desc")
        assert provider.to_config_dict()["description"] == "My desc"

    def test_env_included_when_set(self):
        provider = McpServer(mcp_server_id="p", mode="subprocess", command=["x"], env={"FOO": "bar"})
        assert provider.to_config_dict()["env"] == {"FOO": "bar"}

    def test_empty_env_omitted(self):
        provider = McpServer(mcp_server_id="p", mode="subprocess", command=["x"])
        cfg = provider.to_config_dict()
        assert "env" not in cfg


# ---------------------------------------------------------------------------
# TestCreateProviderHandler
# ---------------------------------------------------------------------------


class TestCreateProviderHandler:
    """Tests for CreateProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryMcpServerRepository()
        self.event_bus = _make_event_bus()
        self.handler = CreateProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_creates_provider_in_repository(self):
        cmd = CreateMcpServerCommand(mcp_server_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        assert self.repo.exists("p")

    def test_emits_provider_registered_event(self):
        cmd = CreateMcpServerCommand(mcp_server_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, McpServerRegistered)
        assert published_event.mcp_server_id == "p"
        assert published_event.source == "api"
        assert published_event.mode == "subprocess"

    def test_duplicate_raises_validation_error(self):
        cmd = CreateMcpServerCommand(mcp_server_id="p", mode="subprocess", command=["x"])
        self.handler.handle(cmd)
        with pytest.raises(ValidationError):
            self.handler.handle(cmd)

    def test_source_field_propagated(self):
        cmd = CreateMcpServerCommand(mcp_server_id="p", mode="subprocess", command=["x"], source="config")
        self.handler.handle(cmd)
        published_event = self.event_bus.publish.call_args[0][0]
        assert published_event.source == "config"


# ---------------------------------------------------------------------------
# TestUpdateProviderHandler
# ---------------------------------------------------------------------------


class TestUpdateProviderHandler:
    """Tests for UpdateProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryMcpServerRepository()
        self.event_bus = _make_event_bus()
        # Pre-populate repo with a COLD provider
        provider = _make_cold_provider(mcp_server_id="p")
        self.repo.add("p", provider)
        self.handler = UpdateProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_updates_description(self):
        cmd = UpdateMcpServerCommand(mcp_server_id="p", description="new")
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["description"] == "new"

    def test_updates_env(self):
        cmd = UpdateMcpServerCommand(mcp_server_id="p", env={"K": "V"})
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["env"] == {"K": "V"}

    def test_updates_idle_ttl(self):
        cmd = UpdateMcpServerCommand(mcp_server_id="p", idle_ttl_s=600)
        self.handler.handle(cmd)
        provider = self.repo.get("p")
        assert provider.to_config_dict()["idle_ttl_s"] == 600

    def test_emits_provider_updated_event(self):
        cmd = UpdateMcpServerCommand(mcp_server_id="p", description="updated")
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, McpServerUpdated)
        assert published_event.mcp_server_id == "p"
        assert published_event.source == "api"

    def test_missing_provider_raises_not_found(self):
        cmd = UpdateMcpServerCommand(mcp_server_id="nonexistent", description="x")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)


# ---------------------------------------------------------------------------
# TestDeleteProviderHandler
# ---------------------------------------------------------------------------


class TestDeleteProviderHandler:
    """Tests for DeleteProviderHandler."""

    def setup_method(self):
        self.repo = InMemoryMcpServerRepository()
        self.event_bus = _make_event_bus()
        self.handler = DeleteProviderHandler(repository=self.repo, event_bus=self.event_bus)

    def test_removes_from_repository(self):
        provider = _make_cold_provider(mcp_server_id="p")
        self.repo.add("p", provider)
        cmd = DeleteMcpServerCommand(mcp_server_id="p")
        self.handler.handle(cmd)
        assert not self.repo.exists("p")

    def test_emits_provider_deregistered_event(self):
        provider = _make_cold_provider(mcp_server_id="p")
        self.repo.add("p", provider)
        cmd = DeleteMcpServerCommand(mcp_server_id="p")
        self.handler.handle(cmd)
        self.event_bus.publish.assert_called_once()
        published_event = self.event_bus.publish.call_args[0][0]
        assert isinstance(published_event, McpServerDeregistered)
        assert published_event.mcp_server_id == "p"
        assert published_event.source == "api"

    def test_stops_ready_provider_before_delete(self):
        provider = MagicMock(spec=McpServer)
        provider.state = ProviderState.READY
        provider.id = "p"
        self.repo.add("p", provider)
        cmd = DeleteMcpServerCommand(mcp_server_id="p")
        self.handler.handle(cmd)
        provider.shutdown.assert_called_once()

    def test_cold_provider_not_stopped(self):
        provider = MagicMock(spec=McpServer)
        provider.state = ProviderState.COLD
        provider.id = "p"
        self.repo.add("p", provider)
        cmd = DeleteMcpServerCommand(mcp_server_id="p")
        self.handler.handle(cmd)
        provider.shutdown.assert_not_called()

    def test_missing_provider_raises_not_found(self):
        cmd = DeleteMcpServerCommand(mcp_server_id="nonexistent")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)


# ---------------------------------------------------------------------------
# TestCreateGroupHandler
# ---------------------------------------------------------------------------


class TestCreateGroupHandler:
    """Tests for CreateGroupHandler."""

    def setup_method(self):
        self.groups: dict = {}
        self.event_bus = _make_event_bus()
        self.handler = CreateGroupHandler(groups=self.groups, event_bus=self.event_bus)

    def test_creates_group_in_groups_dict(self):
        cmd = CreateGroupCommand(group_id="g", strategy="round_robin")
        self.handler.handle(cmd)
        assert "g" in self.groups

    def test_emits_group_created_event(self):
        cmd = CreateGroupCommand(group_id="g", strategy="round_robin")
        self.handler.handle(cmd)
        # The handler publishes GroupCreated via collect_events() or direct publish
        call_args = [call[0][0] for call in self.event_bus.publish.call_args_list]
        created_events = [e for e in call_args if isinstance(e, GroupCreated)]
        assert len(created_events) == 1
        assert created_events[0].group_id == "g"
        assert created_events[0].strategy == "round_robin"
        assert created_events[0].min_healthy == 1

    def test_duplicate_group_raises_validation_error(self):
        cmd = CreateGroupCommand(group_id="g", strategy="round_robin")
        self.handler.handle(cmd)
        with pytest.raises(ValidationError):
            self.handler.handle(cmd)

    def test_strategy_defaults_to_round_robin(self):
        cmd = CreateGroupCommand(group_id="g")
        self.handler.handle(cmd)
        call_args = [call[0][0] for call in self.event_bus.publish.call_args_list]
        created_events = [e for e in call_args if isinstance(e, GroupCreated)]
        assert created_events[0].strategy == "round_robin"


# ---------------------------------------------------------------------------
# TestUpdateGroupHandler
# ---------------------------------------------------------------------------


class TestUpdateGroupHandler:
    """Tests for UpdateGroupHandler."""

    def setup_method(self):
        self.groups: dict = {}
        self.event_bus = _make_event_bus()
        group = McpServerGroup(group_id="g", description="original", min_healthy=1)
        # Drain the GroupCreated event so it doesn't interfere
        group.collect_events()
        self.groups["g"] = group
        self.handler = UpdateGroupHandler(groups=self.groups, event_bus=self.event_bus)

    def test_updates_description(self):
        cmd = UpdateGroupCommand(group_id="g", description="new desc")
        self.handler.handle(cmd)
        assert self.groups["g"].to_config_dict()["description"] == "new desc"

    def test_updates_min_healthy(self):
        cmd = UpdateGroupCommand(group_id="g", min_healthy=2)
        self.handler.handle(cmd)
        assert self.groups["g"].to_config_dict()["min_healthy"] == 2

    def test_emits_group_updated_event(self):
        cmd = UpdateGroupCommand(group_id="g", description="updated")
        self.handler.handle(cmd)
        call_args = [call[0][0] for call in self.event_bus.publish.call_args_list]
        updated_events = [e for e in call_args if isinstance(e, GroupUpdated)]
        assert len(updated_events) == 1
        assert updated_events[0].group_id == "g"

    def test_missing_group_raises_not_found(self):
        cmd = UpdateGroupCommand(group_id="unknown", description="x")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)

    def test_none_fields_not_updated(self):
        # Pre-condition: description is "original", min_healthy is 1
        cmd = UpdateGroupCommand(group_id="g")  # all None
        self.handler.handle(cmd)
        cfg = self.groups["g"].to_config_dict()
        assert cfg["description"] == "original"
        assert cfg["min_healthy"] == 1


# ---------------------------------------------------------------------------
# TestDeleteGroupHandler
# ---------------------------------------------------------------------------


class TestDeleteGroupHandler:
    """Tests for DeleteGroupHandler."""

    def setup_method(self):
        self.groups: dict = {}
        self.event_bus = _make_event_bus()
        group = McpServerGroup(group_id="g")
        group.collect_events()
        self.groups["g"] = group
        self.handler = DeleteGroupHandler(groups=self.groups, event_bus=self.event_bus)

    def test_removes_group_from_dict(self):
        cmd = DeleteGroupCommand(group_id="g")
        self.handler.handle(cmd)
        assert "g" not in self.groups

    def test_emits_group_deleted_event(self):
        cmd = DeleteGroupCommand(group_id="g")
        self.handler.handle(cmd)
        call_args = [call[0][0] for call in self.event_bus.publish.call_args_list]
        deleted_events = [e for e in call_args if isinstance(e, GroupDeleted)]
        assert len(deleted_events) == 1
        assert deleted_events[0].group_id == "g"

    def test_missing_group_raises_not_found(self):
        cmd = DeleteGroupCommand(group_id="unknown")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)


# ---------------------------------------------------------------------------
# TestAddGroupMemberHandler
# ---------------------------------------------------------------------------


class TestAddGroupMemberHandler:
    """Tests for AddGroupMemberHandler."""

    def setup_method(self):
        self.repo = InMemoryMcpServerRepository()
        self.groups: dict = {}
        self.event_bus = _make_event_bus()
        # Pre-populate provider
        provider = _make_cold_provider(mcp_server_id="p")
        self.repo.add("p", provider)
        # Pre-populate group (with auto_start=False to avoid provider I/O)
        group = McpServerGroup(group_id="g", auto_start=False)
        group.collect_events()
        self.groups["g"] = group
        self.handler = AddGroupMemberHandler(repository=self.repo, groups=self.groups, event_bus=self.event_bus)

    def test_adds_member_to_group(self):
        cmd = AddGroupMemberCommand(group_id="g", mcp_server_id="p")
        self.handler.handle(cmd)
        group = self.groups["g"]
        member_ids = [m.id for m in group.members]
        assert "p" in member_ids

    def test_missing_provider_raises_not_found(self):
        cmd = AddGroupMemberCommand(group_id="g", mcp_server_id="nonexistent")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)

    def test_missing_group_raises_not_found(self):
        cmd = AddGroupMemberCommand(group_id="unknown", mcp_server_id="p")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)


# ---------------------------------------------------------------------------
# TestRemoveGroupMemberHandler
# ---------------------------------------------------------------------------


class TestRemoveGroupMemberHandler:
    """Tests for RemoveGroupMemberHandler."""

    def setup_method(self):
        self.repo = InMemoryMcpServerRepository()
        self.groups: dict = {}
        self.event_bus = _make_event_bus()
        # Pre-populate provider
        provider = _make_cold_provider(mcp_server_id="p")
        self.repo.add("p", provider)
        # Pre-populate group with member p (auto_start=False)
        group = McpServerGroup(group_id="g", auto_start=False)
        group.add_member(provider)
        group.collect_events()
        self.groups["g"] = group
        self.handler = RemoveGroupMemberHandler(groups=self.groups, event_bus=self.event_bus)

    def test_removes_member_from_group(self):
        cmd = RemoveGroupMemberCommand(group_id="g", mcp_server_id="p")
        self.handler.handle(cmd)
        group = self.groups["g"]
        member_ids = [m.id for m in group.members]
        assert "p" not in member_ids

    def test_missing_group_raises_not_found(self):
        cmd = RemoveGroupMemberCommand(group_id="unknown", mcp_server_id="p")
        with pytest.raises(ProviderNotFoundError):
            self.handler.handle(cmd)
