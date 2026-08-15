"""The auth query handlers and their registration on the query bus."""

from datetime import UTC, datetime
from unittest.mock import Mock

from mcp_hangar.domain.value_objects.tool_access_policy import ToolAccessPolicy


class TestGetApiKeysByPrincipalHandler:
    """Tests for GetApiKeysByPrincipalHandler."""

    def test_handle_returns_keys_with_metadata(self):
        from mcp_hangar.auth.queries.handlers import GetApiKeysByPrincipalHandler
        from mcp_hangar.auth.queries.queries import GetApiKeysByPrincipalQuery
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="key-1", principal_id="p1", created_at=now, revoked=False),
            ApiKeyMetadata(key_id="k2", name="key-2", principal_id="p1", created_at=now, revoked=True),
        ]

        handler = GetApiKeysByPrincipalHandler(mock_store)
        result = handler.handle(GetApiKeysByPrincipalQuery(principal_id="p1", include_revoked=True))

        assert result["total"] == 2
        assert result["active"] == 1
        assert len(result["keys"]) == 2

    def test_handle_excludes_revoked(self):
        from mcp_hangar.auth.queries.handlers import GetApiKeysByPrincipalHandler
        from mcp_hangar.auth.queries.queries import GetApiKeysByPrincipalQuery
        from mcp_hangar.domain.contracts.authentication import ApiKeyMetadata

        now = datetime.now(UTC)
        mock_store = Mock()
        mock_store.list_keys.return_value = [
            ApiKeyMetadata(key_id="k1", name="key-1", principal_id="p1", created_at=now, revoked=False),
            ApiKeyMetadata(key_id="k2", name="key-2", principal_id="p1", created_at=now, revoked=True),
        ]

        handler = GetApiKeysByPrincipalHandler(mock_store)
        result = handler.handle(GetApiKeysByPrincipalQuery(principal_id="p1", include_revoked=False))

        assert result["total"] == 1
        assert len(result["keys"]) == 1
        assert result["keys"][0]["key_id"] == "k1"


class TestGetApiKeyCountHandler:
    """Tests for GetApiKeyCountHandler."""

    def test_handle_returns_count(self):
        from mcp_hangar.auth.queries.handlers import GetApiKeyCountHandler
        from mcp_hangar.auth.queries.queries import GetApiKeyCountQuery

        mock_store = Mock()
        mock_store.count_keys.return_value = 3

        handler = GetApiKeyCountHandler(mock_store)
        result = handler.handle(GetApiKeyCountQuery(principal_id="p1"))

        assert result["active_keys"] == 3
        assert result["principal_id"] == "p1"


class TestGetRolesForPrincipalHandler:
    """Tests for GetRolesForPrincipalHandler."""

    def test_handle_returns_roles(self):
        from mcp_hangar.auth.queries.handlers import GetRolesForPrincipalHandler
        from mcp_hangar.auth.queries.queries import GetRolesForPrincipalQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="viewer", permissions=frozenset([Permission(resource_type="provider", action="read")])),
        ]

        handler = GetRolesForPrincipalHandler(mock_store)
        result = handler.handle(GetRolesForPrincipalQuery(principal_id="p1", scope="global"))

        assert result["count"] == 1
        assert result["roles"][0]["name"] == "viewer"


class TestGetRoleHandler:
    """Tests for GetRoleHandler."""

    def test_handle_role_found(self):
        from mcp_hangar.auth.queries.handlers import GetRoleHandler
        from mcp_hangar.auth.queries.queries import GetRoleQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_role.return_value = Role(
            name="admin",
            permissions=frozenset([Permission(resource_type="*", action="*")]),
            description="Full access",
        )

        handler = GetRoleHandler(mock_store)
        result = handler.handle(GetRoleQuery(role_name="admin"))

        assert result["found"] is True
        assert result["role"]["name"] == "admin"
        assert result["role"]["permissions_count"] == 1

    def test_handle_role_not_found(self):
        from mcp_hangar.auth.queries.handlers import GetRoleHandler
        from mcp_hangar.auth.queries.queries import GetRoleQuery

        mock_store = Mock()
        mock_store.get_role.return_value = None

        handler = GetRoleHandler(mock_store)
        result = handler.handle(GetRoleQuery(role_name="ghost"))

        assert result["found"] is False
        assert result["role"] is None


class TestListBuiltinRolesHandler:
    """Tests for ListBuiltinRolesHandler."""

    def test_handle_returns_builtin_roles(self):
        from mcp_hangar.auth.queries.handlers import ListBuiltinRolesHandler
        from mcp_hangar.auth.queries.queries import ListBuiltinRolesQuery
        from mcp_hangar.auth.roles import BUILTIN_ROLES

        handler = ListBuiltinRolesHandler()
        result = handler.handle(ListBuiltinRolesQuery())

        assert result["count"] == len(BUILTIN_ROLES)
        assert len(result["roles"]) == len(BUILTIN_ROLES)


class TestCheckPermissionHandler:
    """Tests for CheckPermissionHandler."""

    def test_permission_granted(self):
        from mcp_hangar.auth.queries.handlers import CheckPermissionHandler
        from mcp_hangar.auth.queries.queries import CheckPermissionQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="admin", permissions=frozenset([Permission(resource_type="*", action="*")])),
        ]

        handler = CheckPermissionHandler(mock_store)
        result = handler.handle(
            CheckPermissionQuery(
                principal_id="p1",
                action="read",
                resource_type="provider",
            )
        )

        assert result["allowed"] is True
        assert result["granted_by_role"] == "admin"

    def test_permission_denied(self):
        from mcp_hangar.auth.queries.handlers import CheckPermissionHandler
        from mcp_hangar.auth.queries.queries import CheckPermissionQuery
        from mcp_hangar.domain.value_objects import Permission, Role

        mock_store = Mock()
        mock_store.get_roles_for_principal.return_value = [
            Role(name="viewer", permissions=frozenset([Permission(resource_type="provider", action="read")])),
        ]

        handler = CheckPermissionHandler(mock_store)
        result = handler.handle(
            CheckPermissionQuery(
                principal_id="p1",
                action="delete",
                resource_type="provider",
            )
        )

        assert result["allowed"] is False
        assert result["granted_by_role"] is None


class TestListAllRolesHandler:
    """Tests for ListAllRolesHandler."""

    def test_handle_with_builtin(self):
        from mcp_hangar.auth.queries.handlers import ListAllRolesHandler
        from mcp_hangar.auth.queries.queries import ListAllRolesQuery
        from mcp_hangar.auth.roles import BUILTIN_ROLES
        from mcp_hangar.domain.value_objects import Role

        mock_store = Mock()
        mock_store.list_all_roles.return_value = [
            Role(name="custom-a", permissions=frozenset(), description="A"),
        ]

        handler = ListAllRolesHandler(mock_store)
        result = handler.handle(ListAllRolesQuery(include_builtin=True))

        assert result["builtin_count"] == len(BUILTIN_ROLES)
        assert result["custom_count"] == 1
        assert result["total"] == len(BUILTIN_ROLES) + 1

    def test_handle_without_builtin(self):
        from mcp_hangar.auth.queries.handlers import ListAllRolesHandler
        from mcp_hangar.auth.queries.queries import ListAllRolesQuery
        from mcp_hangar.domain.value_objects import Role

        mock_store = Mock()
        mock_store.list_all_roles.return_value = [
            Role(name="custom-a", permissions=frozenset(), description="A"),
        ]

        handler = ListAllRolesHandler(mock_store)
        result = handler.handle(ListAllRolesQuery(include_builtin=False))

        assert result["builtin_count"] == 0
        assert result["custom_count"] == 1


class TestListPrincipalsHandler:
    """Tests for ListPrincipalsHandler."""

    def test_handle_with_list_principals_method(self):
        from mcp_hangar.auth.queries.handlers import ListPrincipalsHandler
        from mcp_hangar.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock()
        mock_store.list_principals.return_value = [
            {"principal_id": "p1", "roles": ["admin"]},
            {"principal_id": "p2", "roles": ["viewer"]},
        ]

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 2

    def test_handle_with_assignments_dict_fallback(self):
        from mcp_hangar.auth.queries.handlers import ListPrincipalsHandler
        from mcp_hangar.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock(spec=[])  # no list_principals
        mock_store._assignments = {
            "p1": {"global": {"admin", "viewer"}, "tenant:x": {"developer"}},
            "p2": {"global": {"viewer"}},
        }

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 2
        principals_ids = {p["principal_id"] for p in result["principals"]}
        assert principals_ids == {"p1", "p2"}

    def test_handle_no_method_no_assignments(self):
        from mcp_hangar.auth.queries.handlers import ListPrincipalsHandler
        from mcp_hangar.auth.queries.queries import ListPrincipalsQuery

        mock_store = Mock(spec=[])  # no list_principals, no _assignments

        handler = ListPrincipalsHandler(mock_store)
        result = handler.handle(ListPrincipalsQuery())

        assert result["total"] == 0
        assert result["principals"] == []


class TestGetToolAccessPolicyHandler:
    """Tests for GetToolAccessPolicyHandler."""

    def test_handle_policy_found(self):
        from mcp_hangar.auth.queries.handlers import GetToolAccessPolicyHandler
        from mcp_hangar.auth.queries.queries import GetToolAccessPolicyQuery

        mock_store = Mock()
        mock_store.get_policy.return_value = ToolAccessPolicy(
            allow_list=("add", "sub"),
            deny_list=("rm",),
        )

        handler = GetToolAccessPolicyHandler(mock_store)
        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="math"))

        assert result["found"] is True
        assert result["allow_list"] == ["add", "sub"]
        assert result["deny_list"] == ["rm"]

    def test_handle_policy_not_found(self):
        from mcp_hangar.auth.queries.handlers import GetToolAccessPolicyHandler
        from mcp_hangar.auth.queries.queries import GetToolAccessPolicyQuery

        mock_store = Mock()
        mock_store.get_policy.return_value = None

        handler = GetToolAccessPolicyHandler(mock_store)
        result = handler.handle(GetToolAccessPolicyQuery(scope="provider", target_id="ghost"))

        assert result["found"] is False
        assert result["allow_list"] == []
        assert result["deny_list"] == []


class TestRegisterAuthQueryHandlers:
    """Tests for register_auth_query_handlers function."""

    def test_register_all_handlers(self):
        from mcp_hangar.auth.queries.handlers import register_auth_query_handlers
        from mcp_hangar.auth.queries.queries import (
            CheckPermissionQuery,
            GetApiKeyCountQuery,
            GetApiKeysByPrincipalQuery,
            GetRoleQuery,
            GetRolesForPrincipalQuery,
            GetToolAccessPolicyQuery,
            ListAllRolesQuery,
            ListBuiltinRolesQuery,
            ListPrincipalsQuery,
        )

        mock_bus = Mock()
        mock_api_key_store = Mock()
        mock_role_store = Mock()
        mock_tap_store = Mock()

        register_auth_query_handlers(
            mock_bus,
            api_key_store=mock_api_key_store,
            role_store=mock_role_store,
            tap_store=mock_tap_store,
        )

        # Check register was called for each query type
        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert GetApiKeysByPrincipalQuery in registered_types
        assert GetApiKeyCountQuery in registered_types
        assert GetRolesForPrincipalQuery in registered_types
        assert GetRoleQuery in registered_types
        assert CheckPermissionQuery in registered_types
        assert ListAllRolesQuery in registered_types
        assert ListPrincipalsQuery in registered_types
        assert ListBuiltinRolesQuery in registered_types
        assert GetToolAccessPolicyQuery in registered_types

    def test_register_with_none_stores(self):
        from mcp_hangar.auth.queries.handlers import register_auth_query_handlers
        from mcp_hangar.auth.queries.queries import ListBuiltinRolesQuery

        mock_bus = Mock()
        register_auth_query_handlers(mock_bus)

        # Only ListBuiltinRolesQuery should be registered (no store needed)
        registered_types = {c[0][0] for c in mock_bus.register.call_args_list}
        assert ListBuiltinRolesQuery in registered_types
        assert len(registered_types) == 1
