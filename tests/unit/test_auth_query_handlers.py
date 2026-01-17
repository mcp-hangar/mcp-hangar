"""Tests for Auth CQRS query handlers."""

from unittest.mock import Mock

import pytest

from mcp_hangar.application.queries.auth_handlers import (
    CheckPermissionHandler,
    GetApiKeyCountHandler,
    GetApiKeysByPrincipalHandler,
    GetRoleHandler,
    GetRolesForPrincipalHandler,
    ListBuiltinRolesHandler,
    register_auth_query_handlers,
)
from mcp_hangar.application.queries.auth_queries import (
    CheckPermissionQuery,
    GetApiKeyCountQuery,
    GetApiKeysByPrincipalQuery,
    GetRoleQuery,
    GetRolesForPrincipalQuery,
    ListBuiltinRolesQuery,
)
from mcp_hangar.infrastructure.auth.api_key_authenticator import InMemoryApiKeyStore
from mcp_hangar.infrastructure.auth.rbac_authorizer import InMemoryRoleStore


class TestGetApiKeysByPrincipalHandler:
    """Tests for GetApiKeysByPrincipalHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryApiKeyStore()
        store.create_key("test-user", "Key 1")
        store.create_key("test-user", "Key 2")
        # Revoke one
        keys = store.list_keys("test-user")
        store.revoke_key(keys[0].key_id)
        return store

    @pytest.fixture
    def handler(self, store):
        return GetApiKeysByPrincipalHandler(store)

    def test_get_all_keys(self, handler):
        """Query returns all keys including revoked."""
        query = GetApiKeysByPrincipalQuery(
            principal_id="test-user",
            include_revoked=True,
        )

        result = handler.handle(query)

        assert result["total"] == 2
        assert result["active"] == 1

    def test_get_only_active_keys(self, handler):
        """Query can filter out revoked keys."""
        query = GetApiKeysByPrincipalQuery(
            principal_id="test-user",
            include_revoked=False,
        )

        result = handler.handle(query)

        assert len(result["keys"]) == 1
        assert result["keys"][0]["revoked"] is False


class TestGetApiKeyCountHandler:
    """Tests for GetApiKeyCountHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryApiKeyStore()
        store.create_key("test-user", "Key 1")
        store.create_key("test-user", "Key 2")
        return store

    @pytest.fixture
    def handler(self, store):
        return GetApiKeyCountHandler(store)

    def test_count_keys(self, handler):
        """Query returns count of active keys."""
        query = GetApiKeyCountQuery(principal_id="test-user")

        result = handler.handle(query)

        assert result["active_keys"] == 2


class TestGetRolesForPrincipalHandler:
    """Tests for GetRolesForPrincipalHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        store.assign_role("user-1", "developer")
        store.assign_role("user-1", "viewer")
        return store

    @pytest.fixture
    def handler(self, store):
        return GetRolesForPrincipalHandler(store)

    def test_get_roles(self, handler):
        """Query returns all roles for principal."""
        query = GetRolesForPrincipalQuery(principal_id="user-1")

        result = handler.handle(query)

        assert result["count"] == 2
        role_names = {r["name"] for r in result["roles"]}
        assert role_names == {"developer", "viewer"}


class TestGetRoleHandler:
    """Tests for GetRoleHandler."""

    @pytest.fixture
    def store(self):
        return InMemoryRoleStore()

    @pytest.fixture
    def handler(self, store):
        return GetRoleHandler(store)

    def test_get_existing_role(self, handler):
        """Query returns role details."""
        query = GetRoleQuery(role_name="admin")

        result = handler.handle(query)

        assert result["found"] is True
        assert result["role"]["name"] == "admin"

    def test_get_nonexistent_role(self, handler):
        """Query returns not found for missing role."""
        query = GetRoleQuery(role_name="nonexistent")

        result = handler.handle(query)

        assert result["found"] is False


class TestListBuiltinRolesHandler:
    """Tests for ListBuiltinRolesHandler."""

    def test_list_builtin_roles(self):
        """Query returns all built-in roles."""
        handler = ListBuiltinRolesHandler()
        query = ListBuiltinRolesQuery()

        result = handler.handle(query)

        assert result["count"] >= 4  # admin, developer, viewer, auditor
        role_names = {r["name"] for r in result["roles"]}
        assert "admin" in role_names
        assert "developer" in role_names


class TestCheckPermissionHandler:
    """Tests for CheckPermissionHandler."""

    @pytest.fixture
    def store(self):
        store = InMemoryRoleStore()
        store.assign_role("user-1", "developer")
        return store

    @pytest.fixture
    def handler(self, store):
        return CheckPermissionHandler(store)

    def test_check_allowed_permission(self, handler):
        """Query returns allowed for granted permission."""
        query = CheckPermissionQuery(
            principal_id="user-1",
            action="invoke",
            resource_type="tool",
        )

        result = handler.handle(query)

        assert result["allowed"] is True
        assert result["granted_by_role"] == "developer"

    def test_check_denied_permission(self, handler):
        """Query returns denied for missing permission."""
        query = CheckPermissionQuery(
            principal_id="user-1",
            action="delete",
            resource_type="provider",
        )

        result = handler.handle(query)

        assert result["allowed"] is False
        assert result["granted_by_role"] is None


class TestRegisterAuthQueryHandlers:
    """Tests for handler registration."""

    def test_register_handlers(self):
        """All handlers are registered with query bus."""
        query_bus = Mock()
        api_key_store = InMemoryApiKeyStore()
        role_store = InMemoryRoleStore()

        register_auth_query_handlers(
            query_bus=query_bus,
            api_key_store=api_key_store,
            role_store=role_store,
        )

        # Should have registered: 2 api_key + 3 role + 1 builtin = 6 handlers
        assert query_bus.register.call_count == 6

    def test_builtin_roles_always_registered(self):
        """ListBuiltinRolesQuery is always registered."""
        query_bus = Mock()

        register_auth_query_handlers(
            query_bus=query_bus,
            api_key_store=None,
            role_store=None,
        )

        # Only builtin roles handler
        assert query_bus.register.call_count == 1
