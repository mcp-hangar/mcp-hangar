"""Tests for Auth CQRS command handlers."""

from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest

from mcp_hangar.application.commands.auth_commands import (
    AssignRoleCommand,
    CreateApiKeyCommand,
    CreateCustomRoleCommand,
    ListApiKeysCommand,
    RevokeApiKeyCommand,
    RevokeRoleCommand,
)
from mcp_hangar.application.commands.auth_handlers import (
    AssignRoleHandler,
    CreateApiKeyHandler,
    CreateCustomRoleHandler,
    ListApiKeysHandler,
    register_auth_command_handlers,
    RevokeApiKeyHandler,
    RevokeRoleHandler,
)
from mcp_hangar.infrastructure.auth.api_key_authenticator import InMemoryApiKeyStore
from mcp_hangar.infrastructure.auth.rbac_authorizer import InMemoryRoleStore


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
        expires_at = datetime.now(timezone.utc) + timedelta(days=30)

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
    def handler(self, store):
        return CreateCustomRoleHandler(store)

    def test_create_custom_role(self, store, handler):
        """Creating a custom role adds it to store."""
        command = CreateCustomRoleCommand(
            role_name="custom-role",
            description="A custom role",
            permissions=frozenset(["tool:invoke:math", "provider:read:*"]),
            created_by="admin",
        )

        result = handler.handle(command)

        assert result["created"] is True
        assert result["role_name"] == "custom-role"
        assert result["permissions_count"] == 2

        # Verify in store
        role = store.get_role("custom-role")
        assert role is not None
        assert role.description == "A custom role"


class TestRegisterAuthCommandHandlers:
    """Tests for handler registration."""

    def test_register_handlers(self):
        """All handlers are registered with command bus."""
        command_bus = Mock()
        api_key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        register_auth_command_handlers(
            command_bus=command_bus,
            api_key_store=api_key_store,
            role_store=role_store,
        )

        # Should have registered 6 handlers
        assert command_bus.register.call_count == 6

    def test_register_without_stores(self):
        """Registration skips handlers if stores not provided."""
        command_bus = Mock()

        register_auth_command_handlers(
            command_bus=command_bus,
            api_key_store=None,
            role_store=None,
        )

        # No handlers registered
        assert command_bus.register.call_count == 0
