"""The auth command handlers and their registration on the command bus."""

from datetime import UTC, datetime
from unittest.mock import Mock, patch

import pytest


class TestCreateApiKeyHandler:
    """Tests for CreateApiKeyHandler."""

    def test_handle_creates_key(self):
        from mcp_hangar.auth.commands.commands import CreateApiKeyCommand
        from mcp_hangar.auth.commands.handlers import CreateApiKeyHandler
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.create_key.return_value = "mcp_raw_key"
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="kid1", name="test-key", principal_id="p1", created_at=now),
        ]

        handler = CreateApiKeyHandler(mock_store)
        result = handler.handle(
            CreateApiKeyCommand(
                principal_id="p1",
                name="test-key",
                created_by="admin",
            )
        )

        assert result["raw_key"] == "mcp_raw_key"
        assert result["key_id"] == "kid1"
        assert result["principal_id"] == "p1"
        assert "warning" in result

    def test_handle_key_metadata_not_found(self):
        from mcp_hangar.auth.commands.commands import CreateApiKeyCommand
        from mcp_hangar.auth.commands.handlers import CreateApiKeyHandler

        mock_store = Mock()
        mock_store.create_key.return_value = "mcp_raw"
        mock_store.list_keys.return_value = []

        handler = CreateApiKeyHandler(mock_store)
        result = handler.handle(CreateApiKeyCommand(principal_id="p1", name="k1"))

        assert result["key_id"] is None
        assert result["raw_key"] == "mcp_raw"


class TestRevokeApiKeyHandler:
    """Tests for RevokeApiKeyHandler."""

    def test_handle_revokes(self):
        from mcp_hangar.auth.commands.commands import RevokeApiKeyCommand
        from mcp_hangar.auth.commands.handlers import RevokeApiKeyHandler

        mock_store = Mock()
        mock_store.revoke_key.return_value = True

        handler = RevokeApiKeyHandler(mock_store)
        result = handler.handle(
            RevokeApiKeyCommand(
                key_id="kid1",
                revoked_by="admin",
                reason="compromised",
            )
        )

        assert result["revoked"] is True
        assert result["revoked_by"] == "admin"

    def test_handle_revoke_fails(self):
        from mcp_hangar.auth.commands.commands import RevokeApiKeyCommand
        from mcp_hangar.auth.commands.handlers import RevokeApiKeyHandler

        mock_store = Mock()
        mock_store.revoke_key.return_value = False

        handler = RevokeApiKeyHandler(mock_store)
        result = handler.handle(RevokeApiKeyCommand(key_id="ghost"))

        assert result["revoked"] is False


class TestListApiKeysHandler:
    """Tests for ListApiKeysHandler."""

    def test_handle_lists_keys(self):
        from mcp_hangar.auth.commands.commands import ListApiKeysCommand
        from mcp_hangar.auth.commands.handlers import ListApiKeysHandler
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="n1", principal_id="p1", created_at=now),
        ]

        handler = ListApiKeysHandler(mock_store)
        result = handler.handle(ListApiKeysCommand(principal_id="p1"))

        assert result["count"] == 1
        assert result["keys"][0]["key_id"] == "k1"


class TestAssignRoleHandler:
    """Tests for AssignRoleHandler."""

    def test_handle_assigns(self):
        from mcp_hangar.auth.commands.commands import AssignRoleCommand
        from mcp_hangar.auth.commands.handlers import AssignRoleHandler

        mock_store = Mock()
        handler = AssignRoleHandler(mock_store)
        result = handler.handle(
            AssignRoleCommand(
                principal_id="p1",
                role_name="viewer",
                scope="global",
                assigned_by="admin",
            )
        )

        assert result["assigned"] is True
        mock_store.assign_role.assert_called_once()


class TestRevokeRoleHandler:
    """Tests for RevokeRoleHandler."""

    def test_handle_revokes(self):
        from mcp_hangar.auth.commands.commands import RevokeRoleCommand
        from mcp_hangar.auth.commands.handlers import RevokeRoleHandler

        mock_store = Mock()
        handler = RevokeRoleHandler(mock_store)
        result = handler.handle(
            RevokeRoleCommand(
                principal_id="p1",
                role_name="viewer",
                scope="global",
                revoked_by="admin",
            )
        )

        assert result["revoked"] is True
        mock_store.revoke_role.assert_called_once()


class TestCreateCustomRoleHandler:
    """Tests for CreateCustomRoleHandler."""

    def test_handle_creates_role(self):
        from mcp_hangar.auth.commands.commands import CreateCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import CreateCustomRoleHandler

        mock_store = Mock()
        mock_event_bus = Mock()

        handler = CreateCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(
            CreateCustomRoleCommand(
                role_name="custom-role",
                description="Custom",
                permissions=frozenset(["mcp_server:read", "tool:invoke"]),
                created_by="admin",
            )
        )

        assert result["created"] is True
        assert result["permissions_count"] == 2
        mock_store.add_role.assert_called_once()
        mock_event_bus.publish.assert_called_once()

    def test_handle_no_event_bus(self):
        from mcp_hangar.auth.commands.commands import CreateCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import CreateCustomRoleHandler

        mock_store = Mock()
        handler = CreateCustomRoleHandler(mock_store, event_bus=None)
        result = handler.handle(
            CreateCustomRoleCommand(
                role_name="custom-role",
                permissions=frozenset(["mcp_server:read"]),
            )
        )
        assert result["created"] is True


class TestDeleteCustomRoleHandler:
    """Tests for DeleteCustomRoleHandler."""

    def test_handle_deletes_role(self):
        from mcp_hangar.auth.commands.commands import DeleteCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import DeleteCustomRoleHandler

        mock_store = Mock()
        mock_event_bus = Mock()

        handler = DeleteCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(
            DeleteCustomRoleCommand(
                role_name="my-role",
                deleted_by="admin",
            )
        )

        assert result["deleted"] is True
        mock_store.delete_role.assert_called_once_with("my-role")
        mock_event_bus.publish.assert_called_once()

    def test_handle_builtin_role_propagates_error(self):
        from mcp_hangar.auth.commands.commands import DeleteCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import DeleteCustomRoleHandler
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError

        mock_store = Mock()
        mock_store.delete_role.side_effect = CannotModifyBuiltinRoleError("admin")

        handler = DeleteCustomRoleHandler(mock_store, event_bus=Mock())

        with pytest.raises(CannotModifyBuiltinRoleError):
            handler.handle(DeleteCustomRoleCommand(role_name="admin"))


class TestUpdateCustomRoleHandler:
    """Tests for UpdateCustomRoleHandler."""

    def test_handle_updates_role(self):
        from mcp_hangar.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import UpdateCustomRoleHandler
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.update_role.return_value = Role(
            name="my-role",
            permissions=frozenset([Permission(resource_type="tool", action="invoke")]),
            description="Updated",
        )
        mock_event_bus = Mock()

        handler = UpdateCustomRoleHandler(mock_store, event_bus=mock_event_bus)
        result = handler.handle(
            UpdateCustomRoleCommand(
                role_name="my-role",
                permissions=["tool:invoke"],
                description="Updated",
                updated_by="admin",
            )
        )

        assert result["updated"] is True
        assert result["permissions_count"] == 1
        mock_event_bus.publish.assert_called_once()

    def test_handle_role_not_found_propagates(self):
        from mcp_hangar.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.auth.commands.handlers import UpdateCustomRoleHandler
        from mcp_hangar.domain.exceptions import RoleNotFoundError

        mock_store = Mock()
        mock_store.update_role.side_effect = RoleNotFoundError("ghost")

        handler = UpdateCustomRoleHandler(mock_store, event_bus=Mock())

        with pytest.raises(RoleNotFoundError):
            handler.handle(UpdateCustomRoleCommand(role_name="ghost"))


class TestSetToolAccessPolicyHandler:
    """Tests for SetToolAccessPolicyHandler."""

    def test_handle_provider_scope(self):
        from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
        from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        # No pre-existing policy: the handler reads the current one so a partial
        # update cannot drop approval_list (see SetToolAccessPolicyHandler).
        mock_resolver.get_configured_policy.return_value = None
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            result = handler.handle(
                SetToolAccessPolicyCommand(
                    scope="provider",
                    target_id="math",
                    allow_list=["add"],
                    deny_list=["rm"],
                )
            )

        assert result["set"] is True
        mock_tap_store.set_policy.assert_called_once()
        mock_resolver.set_mcp_server_policy.assert_called_once()
        mock_event_bus.publish.assert_called_once()

    def test_handle_group_scope(self):
        from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
        from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        # No pre-existing policy: the handler reads the current one so a partial
        # update cannot drop approval_list (see SetToolAccessPolicyHandler).
        mock_resolver.get_configured_policy.return_value = None
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            handler.handle(
                SetToolAccessPolicyCommand(
                    scope="group",
                    target_id="grp1",
                    allow_list=[],
                    deny_list=["x"],
                )
            )

        mock_resolver.set_group_policy.assert_called_once()

    def test_handle_member_scope_with_colon_format(self):
        from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
        from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        # No pre-existing policy: the handler reads the current one so a partial
        # update cannot drop approval_list (see SetToolAccessPolicyHandler).
        mock_resolver.get_configured_policy.return_value = None
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            handler.handle(
                SetToolAccessPolicyCommand(
                    scope="member",
                    target_id="grp1:member1",
                    allow_list=["add"],
                    deny_list=[],
                )
            )

        mock_resolver.set_member_policy.assert_called_once_with(
            "grp1",
            "member1",
            mock_resolver.set_member_policy.call_args[0][2],
        )

    def test_handle_member_scope_without_colon(self):
        from mcp_hangar.auth.commands.commands import SetToolAccessPolicyCommand
        from mcp_hangar.auth.commands.handlers import SetToolAccessPolicyHandler

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = SetToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)

        mock_resolver = Mock()
        # No pre-existing policy: the handler reads the current one so a partial
        # update cannot drop approval_list (see SetToolAccessPolicyHandler).
        mock_resolver.get_configured_policy.return_value = None
        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            return_value=mock_resolver,
        ):
            handler.handle(
                SetToolAccessPolicyCommand(
                    scope="member",
                    target_id="single_id",
                    allow_list=[],
                    deny_list=[],
                )
            )

        mock_resolver.set_member_policy.assert_called_once_with(
            "single_id",
            "single_id",
            mock_resolver.set_member_policy.call_args[0][2],
        )


class TestClearToolAccessPolicyHandler:
    """Tests for ClearToolAccessPolicyHandler."""

    def test_handle_clears_policy(self):
        from mcp_hangar.auth.commands.commands import ClearToolAccessPolicyCommand
        from mcp_hangar.auth.commands.handlers import ClearToolAccessPolicyHandler

        mock_tap_store = Mock()
        mock_event_bus = Mock()

        handler = ClearToolAccessPolicyHandler(mock_tap_store, event_bus=mock_event_bus)
        result = handler.handle(ClearToolAccessPolicyCommand(scope="provider", target_id="math"))

        assert result["cleared"] is True
        mock_tap_store.clear_policy.assert_called_once_with(scope="provider", target_id="math")
        mock_event_bus.publish.assert_called_once()


class TestRegisterAuthCommandHandlers:
    """Tests for register_auth_command_handlers function."""

    def test_register_all_handlers(self):
        from mcp_hangar.auth.commands.commands import (
            AssignRoleCommand,
            ClearToolAccessPolicyCommand,
            CreateApiKeyCommand,
            CreateCustomRoleCommand,
            DeleteCustomRoleCommand,
            ListApiKeysCommand,
            RevokeApiKeyCommand,
            RevokeRoleCommand,
            SetToolAccessPolicyCommand,
            UpdateCustomRoleCommand,
        )
        from mcp_hangar.auth.commands.handlers import register_auth_command_handlers

        mock_bus = Mock()
        register_auth_command_handlers(
            mock_bus,
            api_key_store=Mock(),
            role_store=Mock(),
            tap_store=Mock(),
            event_bus=Mock(),
        )

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert CreateApiKeyCommand in registered_types
        assert RevokeApiKeyCommand in registered_types
        assert ListApiKeysCommand in registered_types
        assert AssignRoleCommand in registered_types
        assert RevokeRoleCommand in registered_types
        assert CreateCustomRoleCommand in registered_types
        assert DeleteCustomRoleCommand in registered_types
        assert UpdateCustomRoleCommand in registered_types
        assert SetToolAccessPolicyCommand in registered_types
        assert ClearToolAccessPolicyCommand in registered_types

    def test_register_with_none_stores(self):
        from mcp_hangar.auth.commands.handlers import register_auth_command_handlers

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus)

        # No handlers should be registered when all stores are None
        mock_bus.register.assert_not_called()

    def test_register_only_api_key_store(self):
        from mcp_hangar.auth.commands.commands import CreateApiKeyCommand, ListApiKeysCommand, RevokeApiKeyCommand
        from mcp_hangar.auth.commands.handlers import register_auth_command_handlers

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus, api_key_store=Mock())

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert CreateApiKeyCommand in registered_types
        assert RevokeApiKeyCommand in registered_types
        assert ListApiKeysCommand in registered_types
        assert len(registered_types) == 3

    def test_register_only_role_store(self):
        from mcp_hangar.auth.commands.commands import (
            AssignRoleCommand,
            CreateCustomRoleCommand,
            DeleteCustomRoleCommand,
            RevokeRoleCommand,
            UpdateCustomRoleCommand,
        )
        from mcp_hangar.auth.commands.handlers import register_auth_command_handlers

        mock_bus = Mock()
        register_auth_command_handlers(mock_bus, role_store=Mock(), event_bus=Mock())

        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert AssignRoleCommand in registered_types
        assert RevokeRoleCommand in registered_types
        assert CreateCustomRoleCommand in registered_types
        assert DeleteCustomRoleCommand in registered_types
        assert UpdateCustomRoleCommand in registered_types
        assert len(registered_types) == 5
