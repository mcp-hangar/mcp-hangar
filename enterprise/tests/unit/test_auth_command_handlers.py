"""Tests for Auth CQRS command handlers."""

from datetime import datetime, timedelta, UTC
from unittest.mock import Mock

import pytest

from enterprise.auth.commands.commands import (
    AssignRoleCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    ListApiKeysCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
)
from enterprise.auth.commands.handlers import (
    AssignRoleHandler,
    CreateApiKeyHandler,
    CreateCustomRoleHandler,
    ListApiKeysHandler,
    register_auth_command_handlers,
    RevokeApiKeyHandler,
    RevokeRoleHandler,
)
from enterprise.auth.infrastructure.api_key_authenticator import InMemoryApiKeyStore
from enterprise.auth.infrastructure.rbac_authorizer import InMemoryRoleStore


class TestCreateApiKeyHandler:
    """Tests for CreateApiKeyHandler."""

    @pytest.fixture
    def store(self):
        return InMemoryApiKeyStore()

    @pytest.fixture
    def handler(self, store):
        return CreateApiKeyHandler(store)

    def test_create_key_returns_raw_key(self, handler):
        """Creating a key returns the raw key value."""
        command = CreateApiKeyCommand(
            principal_id="test-user",
            name="Test Key",
            created_by="admin",
        )

        result = handler.handle(command)

        assert "raw_key" in result
        assert result["raw_key"].startswith("mcp_")
        assert result["principal_id"] == "test-user"
        assert result["name"] == "Test Key"
        assert "warning" in result

    def test_create_key_with_expiration(self, handler):
        """Creating a key with expiration includes it in result."""
        expires_at = datetime.now(UTC) + timedelta(days=30)

        command = CreateApiKeyCommand(
            principal_id="test-user",
            name="Expiring Key",
            expires_at=expires_at,
        )

        result = handler.handle(command)

        assert result["expires_at"] is not None


class TestRevokeApiKeyHandler:
    """Tests for RevokeApiKeyHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryApiKeyStore()
        # Create a key to revoke
        store.create_key("test-user", "To Revoke")
        return store

    @pytest.fixture
    def handler(self, store):
        return RevokeApiKeyHandler(store)

    def test_revoke_key_success(self, store, handler):
        """Revoking a key returns success."""
        keys = store.list_keys("test-user")
        key_id = keys[0].key_id

        command = RevokeApiKeyCommand(
            key_id=key_id,
            revoked_by="admin",
            reason="Testing",
        )

        result = handler.handle(command)

        assert result["revoked"] is True
        assert result["key_id"] == key_id
        assert result["revoked_by"] == "admin"
        assert result["reason"] == "Testing"

    def test_revoke_nonexistent_key(self, handler):
        """Revoking nonexistent key returns failure."""
        command = RevokeApiKeyCommand(key_id="nonexistent")

        result = handler.handle(command)

        assert result["revoked"] is False


class TestListApiKeysHandler:
    """Tests for ListApiKeysHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryApiKeyStore()
        store.create_key("test-user", "Key 1")
        store.create_key("test-user", "Key 2")
        return store

    @pytest.fixture
    def handler(self, store):
        return ListApiKeysHandler(store)

    def test_list_keys(self, handler):
        """Listing keys returns all keys for principal."""
        command = ListApiKeysCommand(principal_id="test-user")

        result = handler.handle(command)

        assert result["principal_id"] == "test-user"
        assert result["count"] == 2
        assert len(result["keys"]) == 2


class TestAssignRoleHandler:
    """Tests for AssignRoleHandler."""

    @pytest.fixture
    def store(self):
        return InMemoryRoleStore()

    @pytest.fixture
    def handler(self, store):
        return AssignRoleHandler(store)

    def test_assign_role(self, handler):
        """Assigning a role returns confirmation."""
        command = AssignRoleCommand(
            principal_id="user-1",
            role_name="developer",
            scope="global",
            assigned_by="admin",
        )

        result = handler.handle(command)

        assert result["assigned"] is True
        assert result["principal_id"] == "user-1"
        assert result["role_name"] == "developer"
        assert result["assigned_by"] == "admin"

    def test_assign_scoped_role(self, handler):
        """Assigning a scoped role includes scope."""
        command = AssignRoleCommand(
            principal_id="user-1",
            role_name="developer",
            scope="tenant:team-a",
        )

        result = handler.handle(command)

        assert result["scope"] == "tenant:team-a"


class TestRevokeRoleHandler:
    """Tests for RevokeRoleHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        store.assign_role("user-1", "developer")
        return store

    @pytest.fixture
    def handler(self, store):
        return RevokeRoleHandler(store)

    def test_revoke_role(self, handler):
        """Revoking a role returns confirmation."""
        command = RevokeRoleCommand(
            principal_id="user-1",
            role_name="developer",
            revoked_by="admin",
        )

        result = handler.handle(command)

        assert result["revoked"] is True
        assert result["principal_id"] == "user-1"
        assert result["revoked_by"] == "admin"


class TestCreateCustomRoleHandler:
    """Tests for CreateCustomRoleHandler."""

    @pytest.fixture
    def store(self):
        return InMemoryRoleStore()

    @pytest.fixture
    def handler_no_bus(self, store):
        return CreateCustomRoleHandler(store)

    @pytest.fixture
    def handler_with_bus(self, store):
        from unittest.mock import Mock

        return CreateCustomRoleHandler(store, event_bus=Mock())

    def test_create_custom_role(self, store, handler_no_bus):
        """Creating a custom role adds it to store."""
        command = CreateCustomRoleCommand(
            role_name="custom-role",
            description="A custom role",
            permissions=frozenset(["tool:invoke:math", "provider:read:*"]),
            created_by="admin",
        )

        result = handler_no_bus.handle(command)

        assert result["created"] is True
        assert result["role_name"] == "custom-role"
        assert result["permissions_count"] == 2

        # Verify in store
        role = store.get_role("custom-role")
        assert role is not None
        assert role.description == "A custom role"

    def test_create_custom_role_emits_event_when_bus_provided(self, store, handler_with_bus):
        """CreateCustomRoleHandler emits CustomRoleCreated when event_bus is given."""
        from enterprise.auth.commands.handlers import CreateCustomRoleHandler
        from mcp_hangar.domain.events import CustomRoleCreated

        bus = Mock()
        handler = CreateCustomRoleHandler(store, event_bus=bus)
        command = CreateCustomRoleCommand(
            role_name="evt-role",
            description="Event test",
            permissions=frozenset(["tool:invoke:*"]),
            created_by="admin",
        )

        handler.handle(command)

        bus.publish.assert_called_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, CustomRoleCreated)
        assert event.role_name == "evt-role"

    def test_create_custom_role_no_event_when_no_bus(self, store, handler_no_bus):
        """CreateCustomRoleHandler does not raise when event_bus is None."""
        command = CreateCustomRoleCommand(
            role_name="no-bus-role",
            permissions=frozenset(),
            created_by="system",
        )
        result = handler_no_bus.handle(command)
        assert result["created"] is True


class TestRegisterAuthCommandHandlers:
    """Tests for handler registration."""

    def test_register_handlers_with_all_stores(self):
        """All handlers are registered when all stores provided."""
        command_bus = Mock()
        api_key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()
        tap_store = Mock()
        event_bus = Mock()

        register_auth_command_handlers(
            command_bus=command_bus,
            api_key_store=api_key_store,
            role_store=role_store,
            tap_store=tap_store,
            event_bus=event_bus,
        )

        # 3 api_key + 5 role + 2 tap = 10 total
        assert command_bus.register.call_count == 10

    def test_register_handlers_role_only(self):
        """Only role handlers registered when only role_store provided."""
        command_bus = Mock()
        role_store = InMemoryRoleStore()

        register_auth_command_handlers(
            command_bus=command_bus,
            api_key_store=None,
            role_store=role_store,
        )

        # 5 role handlers
        assert command_bus.register.call_count == 5

    def test_register_without_stores(self):
        """Registration skips handlers if stores not provided."""
        command_bus = Mock()

        register_auth_command_handlers(
            command_bus=command_bus,
            api_key_store=None,
            role_store=None,
        )

        assert command_bus.register.call_count == 0


class TestDeleteCustomRoleHandler:
    """Tests for DeleteCustomRoleHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        from mcp_hangar.domain.value_objects import Permission, Role

        store.add_role(
            Role(
                name="to-delete",
                description="Will be deleted",
                permissions=frozenset([Permission("tool", "invoke", "*")]),
            )
        )
        return store

    @pytest.fixture
    def event_bus(self):
        return Mock()

    @pytest.fixture
    def handler(self, store, event_bus):
        from enterprise.auth.commands.handlers import DeleteCustomRoleHandler

        return DeleteCustomRoleHandler(store, event_bus)

    def test_delete_role_returns_confirmation(self, handler):
        """Deleting a custom role returns confirmation dict."""
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand

        command = DeleteCustomRoleCommand(role_name="to-delete", deleted_by="admin")
        result = handler.handle(command)

        assert result["deleted"] is True
        assert result["role_name"] == "to-delete"
        assert result["deleted_by"] == "admin"

    def test_delete_role_emits_event(self, handler, event_bus):
        """Deleting a custom role emits CustomRoleDeleted."""
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand
        from mcp_hangar.domain.events import CustomRoleDeleted

        command = DeleteCustomRoleCommand(role_name="to-delete")
        handler.handle(command)

        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args[0][0]
        assert isinstance(event, CustomRoleDeleted)
        assert event.role_name == "to-delete"

    def test_delete_builtin_role_propagates_error(self, handler):
        """CannotModifyBuiltinRoleError propagates from store."""
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand
        from mcp_hangar.domain.exceptions import CannotModifyBuiltinRoleError

        command = DeleteCustomRoleCommand(role_name="admin")
        with pytest.raises(CannotModifyBuiltinRoleError):
            handler.handle(command)


class TestUpdateCustomRoleHandler:
    """Tests for UpdateCustomRoleHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        from mcp_hangar.domain.value_objects import Permission, Role

        store.add_role(
            Role(
                name="to-update",
                description="Original",
                permissions=frozenset([Permission("tool", "invoke", "*")]),
            )
        )
        return store

    @pytest.fixture
    def event_bus(self):
        return Mock()

    @pytest.fixture
    def handler(self, store, event_bus):
        from enterprise.auth.commands.handlers import UpdateCustomRoleHandler

        return UpdateCustomRoleHandler(store, event_bus)

    def test_update_role_returns_confirmation(self, handler):
        """Updating a custom role returns confirmation dict."""
        from enterprise.auth.commands.commands import UpdateCustomRoleCommand

        command = UpdateCustomRoleCommand(
            role_name="to-update",
            permissions=["provider:read:*"],
            description="Updated",
            updated_by="admin",
        )
        result = handler.handle(command)

        assert result["updated"] is True
        assert result["role_name"] == "to-update"
        assert result["updated_by"] == "admin"
        assert result["permissions_count"] == 1

    def test_update_role_emits_event(self, handler, event_bus):
        """Updating a custom role emits CustomRoleUpdated."""
        from enterprise.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.domain.events import CustomRoleUpdated

        command = UpdateCustomRoleCommand(
            role_name="to-update",
            permissions=["provider:read:*"],
        )
        handler.handle(command)

        event_bus.publish.assert_called_once()
        event = event_bus.publish.call_args[0][0]
        assert isinstance(event, CustomRoleUpdated)
        assert event.role_name == "to-update"

    def test_update_unknown_role_propagates_error(self, handler):
        """RoleNotFoundError propagates from store."""
        from enterprise.auth.commands.commands import UpdateCustomRoleCommand
        from mcp_hangar.domain.exceptions import RoleNotFoundError

        command = UpdateCustomRoleCommand(role_name="ghost", permissions=[])
        with pytest.raises(RoleNotFoundError):
            handler.handle(command)
