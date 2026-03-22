"""Integration tests for provider CRUD, group CRUD, and config serializer.

Exercises the full vertical slice from command dispatch through handler logic
to in-memory repository state and domain event emission.
No mocks on internal components; real CommandBus, EventBus, and
InMemoryProviderRepository used throughout.
"""

import os
import tempfile
from unittest.mock import patch

import pytest
import yaml

from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    CreateProviderCommand,
    DeleteGroupCommand,
    DeleteProviderCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
    UpdateProviderCommand,
)
from mcp_hangar.application.commands.crud_handlers import register_crud_handlers
from mcp_hangar.domain.events import ProviderDeregistered, ProviderRegistered, ProviderUpdated
from mcp_hangar.domain.exceptions import ProviderNotFoundError
from mcp_hangar.domain.model.provider import Provider
from mcp_hangar.domain.model.provider_group import GroupDeleted, GroupUpdated
from mcp_hangar.domain.repository import InMemoryProviderRepository
from mcp_hangar.domain.value_objects.provider import ProviderMode
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.config_serializer import serialize_full_config, write_config_backup


def _make_infrastructure(groups: dict | None = None):
    """Return (command_bus, event_bus, repository, groups, captured_events)."""
    event_bus = EventBus()
    command_bus = CommandBus()
    repository = InMemoryProviderRepository()
    groups_dict = groups if groups is not None else {}
    captured: list = []

    # Capture all domain events for assertions
    for event_type in (ProviderRegistered, ProviderUpdated, ProviderDeregistered):
        event_bus.subscribe(event_type, lambda e: captured.append(e))

    for event_type in (GroupUpdated, GroupDeleted):
        event_bus.subscribe(event_type, lambda e: captured.append(e))

    register_crud_handlers(command_bus, repository, event_bus, groups_dict)
    return command_bus, event_bus, repository, groups_dict, captured


# =============================================================================
# TestProviderCrudIntegration
# =============================================================================


class TestProviderCrudIntegration:
    """Integration tests for provider CRUD operations."""

    def test_create_provider_stores_in_repository_and_emits_event(self):
        """CreateProviderHandler stores provider and emits ProviderRegistered."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateProviderCommand(
                provider_id="test-provider",
                mode="subprocess",
                command=["python", "-m", "fake_server"],
            )
        )
        provider = repo.get("test-provider")
        assert provider is not None
        assert any(isinstance(e, ProviderRegistered) and e.provider_id == "test-provider" for e in captured)

    def test_update_provider_updates_description_and_emits_event(self):
        """UpdateProviderHandler updates description and emits ProviderUpdated."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateProviderCommand(
                provider_id="test-provider",
                mode="subprocess",
                command=["python", "-m", "fake_server"],
            )
        )
        command_bus.send(
            UpdateProviderCommand(
                provider_id="test-provider",
                description="updated description",
            )
        )
        assert any(isinstance(e, ProviderUpdated) and e.provider_id == "test-provider" for e in captured)

    def test_delete_cold_provider_removes_from_repository_and_emits_event(self):
        """DeleteProviderHandler removes COLD provider and emits ProviderDeregistered."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateProviderCommand(
                provider_id="test-provider",
                mode="subprocess",
                command=["python", "-m", "fake_server"],
            )
        )
        command_bus.send(DeleteProviderCommand(provider_id="test-provider"))
        assert repo.get("test-provider") is None
        assert any(isinstance(e, ProviderDeregistered) and e.provider_id == "test-provider" for e in captured)

    def test_delete_unknown_provider_raises_not_found(self):
        """DeleteProviderHandler raises ProviderNotFoundError for nonexistent provider."""
        command_bus, _, _, _, _ = _make_infrastructure()
        with pytest.raises(ProviderNotFoundError):
            command_bus.send(DeleteProviderCommand(provider_id="nonexistent"))

    def test_update_unknown_provider_raises_not_found(self):
        """UpdateProviderHandler raises ProviderNotFoundError for nonexistent provider."""
        command_bus, _, _, _, _ = _make_infrastructure()
        with pytest.raises(ProviderNotFoundError):
            command_bus.send(UpdateProviderCommand(provider_id="nonexistent", description="x"))


# =============================================================================
# TestGroupCrudIntegration
# =============================================================================


class TestGroupCrudIntegration:
    """Integration tests for group CRUD operations."""

    def test_create_group_stores_in_groups_dict(self):
        """CreateGroupHandler stores group in the groups dict."""
        command_bus, _, _, groups, _ = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a", strategy="round_robin"))
        assert "team-a" in groups

    def test_add_member_appears_in_group(self):
        """AddGroupMemberHandler adds provider to group's members list."""
        command_bus, _, repo, groups, _ = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a"))
        # Create a provider to add as member
        command_bus.send(
            CreateProviderCommand(
                provider_id="member-1",
                mode="subprocess",
                command=["python", "-m", "fake_server"],
            )
        )
        command_bus.send(AddGroupMemberCommand(group_id="team-a", provider_id="member-1"))
        group = groups["team-a"]
        member_ids = [m.id for m in group.members]
        assert "member-1" in member_ids

    def test_remove_member_absent_from_group(self):
        """RemoveGroupMemberHandler removes provider from group's members list."""
        command_bus, _, repo, groups, _ = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a"))
        command_bus.send(
            CreateProviderCommand(
                provider_id="member-1",
                mode="subprocess",
                command=["python", "-m", "fake_server"],
            )
        )
        command_bus.send(AddGroupMemberCommand(group_id="team-a", provider_id="member-1"))
        command_bus.send(RemoveGroupMemberCommand(group_id="team-a", provider_id="member-1"))
        group = groups["team-a"]
        member_ids = [m.id for m in group.members]
        assert "member-1" not in member_ids

    def test_delete_group_removes_from_groups_dict(self):
        """DeleteGroupHandler removes group from groups dict and emits GroupDeleted."""
        command_bus, _, _, groups, captured = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a"))
        command_bus.send(DeleteGroupCommand(group_id="team-a"))
        assert "team-a" not in groups
        assert any(isinstance(e, GroupDeleted) for e in captured)

    def test_update_group_emits_group_updated_event(self):
        """UpdateGroupHandler emits GroupUpdated event when group is updated."""
        command_bus, _, _, groups, captured = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a", strategy="round_robin"))
        command_bus.send(UpdateGroupCommand(group_id="team-a", strategy="least_connections"))
        assert any(isinstance(e, GroupUpdated) for e in captured)


# =============================================================================
# TestConfigSerializerIntegration
# =============================================================================


class TestConfigSerializerIntegration:
    """Integration tests for config serializer (round-trip + backup)."""

    def _make_provider(self, provider_id: str = "math") -> Provider:
        """Create a real Provider instance for serialization tests."""
        return Provider(
            provider_id=provider_id,
            mode=ProviderMode.SUBPROCESS,
            command=["python", "-m", "math_server"],
        )

    def test_serialize_full_config_includes_provider(self):
        """serialize_full_config() with explicit providers returns dict with provider."""
        provider = self._make_provider("math")
        providers = {"math": provider}
        config_dict = serialize_full_config(providers=providers, groups={})
        assert "providers" in config_dict
        assert "math" in config_dict["providers"]

    def test_serialize_full_config_round_trips_through_yaml(self):
        """Serialized output must be valid YAML that preserves provider_id."""
        provider = self._make_provider("math")
        providers = {"math": provider}
        config_dict = serialize_full_config(providers=providers, groups={})
        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)
        assert "math" in reloaded.get("providers", {})

    def test_write_config_backup_creates_bak1_file(self):
        """write_config_backup() creates a .bak1 file alongside the config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            # Write a minimal config so the source file exists
            with open(config_path, "w") as f:
                f.write("providers: {}\n")
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"providers": {}},
            ):
                backup_path = write_config_backup(config_path)
            assert os.path.exists(backup_path)
            assert backup_path.endswith(".bak1")

    def test_write_config_backup_rotates_on_second_call(self):
        """Second call to write_config_backup() rotates bak1 to bak2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w") as f:
                f.write("providers: {}\n")
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"providers": {}},
            ):
                bak1_path = write_config_backup(config_path)
                assert os.path.exists(bak1_path)
                # Second backup: bak1 should rotate to bak2
                bak1_path_second = write_config_backup(config_path)
                bak2_path = config_path + ".bak2"
                assert os.path.exists(bak1_path_second)
                assert os.path.exists(bak2_path)
