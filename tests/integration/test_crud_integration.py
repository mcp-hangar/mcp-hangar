"""Integration tests for provider CRUD, group CRUD, and config serializer.

Exercises the full vertical slice from command dispatch through handler logic
to in-memory repository state and domain event emission.
No mocks on internal components; real CommandBus, EventBus, and
InMemoryMcpServerRepository used throughout.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml

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
from mcp_hangar.application.commands.crud_handlers import register_crud_handlers
from mcp_hangar.domain.events import McpServerDeregistered, McpServerRegistered, McpServerUpdated
from mcp_hangar.domain.exceptions import ProviderNotFoundError
from mcp_hangar.domain.model.provider import McpServer
from mcp_hangar.domain.model.mcp_server_group import GroupDeleted, GroupUpdated
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provider import ProviderMode
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.config_serializer import serialize_full_config, write_config_backup


def _make_infrastructure(groups: dict | None = None):
    """Return (command_bus, event_bus, repository, groups, captured_events)."""
    event_bus = EventBus()
    command_bus = CommandBus()
    repository = InMemoryMcpServerRepository()
    groups_dict = groups if groups is not None else {}
    captured: list = []

    # Capture all domain events for assertions
    for event_type in (McpServerRegistered, McpServerUpdated, McpServerDeregistered):
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
        """CreateProviderHandler stores provider and emits McpServerRegistered."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateMcpServerCommand(mcp_server_id="test-provider", mode="subprocess",
            command=["python", "-m", "fake_server"],)
        )
        provider = repo.get("test-provider")
        assert provider is not None
        assert any(isinstance(e, McpServerRegistered) and e.mcp_server_id == "test-provider" for e in captured)

    def test_update_provider_updates_description_and_emits_event(self):
        """UpdateProviderHandler updates description and emits McpServerUpdated."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateMcpServerCommand(mcp_server_id="test-provider", mode="subprocess",
            command=["python", "-m", "fake_server"],)
        )
        command_bus.send(
            UpdateMcpServerCommand(mcp_server_id="test-provider", description="updated description",)
        )
        assert any(isinstance(e, McpServerUpdated) and e.mcp_server_id == "test-provider" for e in captured)

    def test_delete_cold_provider_removes_from_repository_and_emits_event(self):
        """DeleteProviderHandler removes COLD provider and emits McpServerDeregistered."""
        command_bus, _, repo, _, captured = _make_infrastructure()
        command_bus.send(
            CreateMcpServerCommand(mcp_server_id="test-provider", mode="subprocess",
            command=["python", "-m", "fake_server"],)
        )
        command_bus.send(DeleteMcpServerCommand(mcp_server_id="test-provider"))
        assert repo.get("test-provider") is None
        assert any(isinstance(e, McpServerDeregistered) and e.mcp_server_id == "test-provider" for e in captured)

    def test_delete_unknown_provider_raises_not_found(self):
        """DeleteProviderHandler raises ProviderNotFoundError for nonexistent provider."""
        command_bus, _, _, _, _ = _make_infrastructure()
        with pytest.raises(ProviderNotFoundError):
            command_bus.send(DeleteMcpServerCommand(mcp_server_id="nonexistent"))

    def test_update_unknown_provider_raises_not_found(self):
        """UpdateProviderHandler raises ProviderNotFoundError for nonexistent provider."""
        command_bus, _, _, _, _ = _make_infrastructure()
        with pytest.raises(ProviderNotFoundError):
            command_bus.send(UpdateMcpServerCommand(mcp_server_id="nonexistent", description="x"))


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
            CreateMcpServerCommand(mcp_server_id="member-1", mode="subprocess",
            command=["python", "-m", "fake_server"],)
        )
        command_bus.send(AddGroupMemberCommand(group_id="team-a", mcp_server_id="member-1"))
        group = groups["team-a"]
        member_ids = [m.id for m in group.members]
        assert "member-1" in member_ids

    def test_remove_member_absent_from_group(self):
        """RemoveGroupMemberHandler removes provider from group's members list."""
        command_bus, _, repo, groups, _ = _make_infrastructure()
        command_bus.send(CreateGroupCommand(group_id="team-a"))
        command_bus.send(
            CreateMcpServerCommand(mcp_server_id="member-1", mode="subprocess",
            command=["python", "-m", "fake_server"],)
        )
        command_bus.send(AddGroupMemberCommand(group_id="team-a", mcp_server_id="member-1"))
        command_bus.send(RemoveGroupMemberCommand(group_id="team-a", mcp_server_id="member-1"))
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

    def _make_provider(self, mcp_server_id: str = "math") -> McpServer:
        """Create a real Provider instance for serialization tests."""
        return McpServer(mcp_server_id=mcp_server_id, mode=ProviderMode.SUBPROCESS,
        command=["python", "-m", "math_server"],)

    def test_serialize_full_config_includes_provider(self):
        """serialize_full_config() with explicit providers returns dict with provider."""
        provider = self._make_provider("math")
        providers = {"math": provider}
        config_dict = serialize_full_config(mcp_servers=providers, groups={})
        assert "mcp_servers" in config_dict
        assert "math" in config_dict["mcp_servers"]

    def test_serialize_full_config_round_trips_through_yaml(self):
        """Serialized output must be valid YAML that preserves mcp_server_id."""
        provider = self._make_provider("math")
        providers = {"math": provider}
        config_dict = serialize_full_config(mcp_servers=providers, groups={})
        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)
        assert "math" in reloaded.get("mcp_servers", {})

    def test_write_config_backup_creates_bak1_file(self):
        """write_config_backup() creates a .bak1 file alongside the config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            # Write a minimal config so the source file exists
            with open(config_path, "w") as f:
                f.write("mcp_servers: {}\n")
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"mcp_servers": {}},
            ):
                backup_path = write_config_backup(config_path)
            assert os.path.exists(backup_path)
            assert backup_path.endswith(".bak1")

    def test_write_config_backup_rotates_on_second_call(self):
        """Second call to write_config_backup() rotates bak1 to bak2."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_path = os.path.join(tmpdir, "config.yaml")
            with open(config_path, "w") as f:
                f.write("mcp_servers: {}\n")
            with patch(
                "mcp_hangar.server.config_serializer.serialize_full_config",
                return_value={"mcp_servers": {}},
            ):
                bak1_path = write_config_backup(config_path)
                assert os.path.exists(bak1_path)
                # Second backup: bak1 should rotate to bak2
                bak1_path_second = write_config_backup(config_path)
                bak2_path = config_path + ".bak2"
                assert os.path.exists(bak1_path_second)
                assert os.path.exists(bak2_path)


# =============================================================================
# TestConfigRoundTripIntegration (Plan 04)
# =============================================================================


class TestConfigRoundTripIntegration:
    """Round-trip integration tests: serialize -> write -> reload -> verify."""

    def _make_provider(self, mcp_server_id: str, mode: str = "subprocess") -> McpServer:
        """Create a real Provider for round-trip tests."""
        return McpServer(mcp_server_id=mcp_server_id, mode=ProviderMode.SUBPROCESS if mode == "subprocess" else ProviderMode.SUBPROCESS,
        command=["python", "-m", f"{mcp_server_id}_server"],)

    def test_serialize_then_yaml_dump_then_safe_load_preserves_mcp_server_id(self):
        """serialize_full_config -> safe_dump -> safe_load round-trip preserves provider id."""
        provider = self._make_provider("analytics")
        config_dict = serialize_full_config(mcp_servers={"analytics": provider}, groups={})

        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)

        assert "analytics" in reloaded["mcp_servers"]

    def test_serialize_then_yaml_dump_then_safe_load_preserves_mode(self):
        """serialize_full_config -> safe_dump -> safe_load round-trip preserves mode field."""
        provider = self._make_provider("analytics")
        config_dict = serialize_full_config(mcp_servers={"analytics": provider}, groups={})

        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)

        assert reloaded["mcp_servers"]["analytics"]["mode"] == "subprocess"

    def test_serialize_then_yaml_dump_then_safe_load_preserves_command(self):
        """serialize_full_config -> safe_dump -> safe_load round-trip preserves command list."""
        provider = self._make_provider("analytics")
        config_dict = serialize_full_config(mcp_servers={"analytics": provider}, groups={})

        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)

        assert reloaded["mcp_servers"]["analytics"]["command"] == ["python", "-m", "analytics_server"]

    def test_serialize_multiple_providers_all_survive_round_trip(self):
        """Multiple providers all survive serialize -> YAML -> reload cycle."""
        providers = {
            "alpha": self._make_provider("alpha"),
            "beta": self._make_provider("beta"),
            "gamma": self._make_provider("gamma"),
        }
        config_dict = serialize_full_config(mcp_servers=providers, groups={})
        yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, allow_unicode=True)
        reloaded = yaml.safe_load(yaml_str)

        for name in ("alpha", "beta", "gamma"):
            assert name in reloaded["mcp_servers"], f"provider {name} missing after round-trip"

    def test_backup_bak1_content_survives_yaml_reload(self, tmp_path: Path):
        """write_config_backup() output can be re-loaded with yaml.safe_load without error."""
        provider = self._make_provider("audit")
        config_dict = serialize_full_config(mcp_servers={"audit": provider}, groups={})

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump({"mcp_servers": {}}))

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_dict,
        ):
            bak1_path = write_config_backup(str(config_file))

        reloaded = yaml.safe_load(Path(bak1_path).read_text())
        assert isinstance(reloaded, dict)
        assert "mcp_servers" in reloaded
        assert "audit" in reloaded["mcp_servers"]

    def test_backup_bak1_snapshot_metadata_is_parseable(self, tmp_path: Path):
        """__snapshot__ metadata in bak1 is a dict with expected keys."""
        provider = self._make_provider("audit")
        config_dict = serialize_full_config(mcp_servers={"audit": provider}, groups={})

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump({"mcp_servers": {}}))

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_dict,
        ):
            bak1_path = write_config_backup(str(config_file))

        reloaded = yaml.safe_load(Path(bak1_path).read_text())
        snapshot = reloaded["__snapshot__"]
        assert "timestamp" in snapshot
        assert "mcp_server_count" in snapshot
        assert "group_count" in snapshot
        assert snapshot["mcp_server_count"] == 1
        assert snapshot["group_count"] == 0

    def test_rotation_preserves_provider_data_in_bak2(self, tmp_path: Path):
        """After two backups, bak2 contains the first backup's provider data."""
        provider_v1 = self._make_provider("v1")
        provider_v2 = self._make_provider("v2")

        config_v1 = serialize_full_config(mcp_servers={"v1": provider_v1}, groups={})
        config_v2 = serialize_full_config(mcp_servers={"v2": provider_v2}, groups={})

        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml.safe_dump({"mcp_servers": {}}))

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_v1,
        ):
            write_config_backup(str(config_file))

        with patch(
            "mcp_hangar.server.config_serializer.serialize_full_config",
            return_value=config_v2,
        ):
            write_config_backup(str(config_file))

        bak2 = yaml.safe_load((tmp_path / "config.yaml.bak2").read_text())
        bak1 = yaml.safe_load((tmp_path / "config.yaml.bak1").read_text())

        assert "v1" in bak2["mcp_servers"], "bak2 should hold first backup content"
        assert "v2" in bak1["mcp_servers"], "bak1 should hold second backup content"

    def test_serialize_full_config_is_deterministic(self):
        """Two calls with the same providers produce identical YAML."""
        provider = self._make_provider("stable")
        providers = {"stable": provider}

        first = yaml.safe_dump(
            serialize_full_config(mcp_servers=providers, groups={}),
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        second = yaml.safe_dump(
            serialize_full_config(mcp_servers=providers, groups={}),
            default_flow_style=False,
            sort_keys=True,
            allow_unicode=True,
        )
        assert first == second
