"""The `/auth/*` REST handlers: keys, roles and policies over HTTP."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestAuthRoutes:
    """Tests for the auth API route handlers."""

    def _make_request(self, body=None, path_params=None, query_params=None):
        request = AsyncMock()
        request.json = AsyncMock(return_value=body or {})
        request.path_params = path_params or {}
        request.query_params = query_params or {}
        return request

    # --- auth_routes list ---

    def test_auth_routes_list_is_not_empty(self):
        from mcp_hangar.auth.api.routes import auth_routes

        assert len(auth_routes) > 0

    def test_auth_routes_all_are_route_instances(self):
        from starlette.routing import Route

        from mcp_hangar.auth.api.routes import auth_routes

        for route in auth_routes:
            assert isinstance(route, Route)

    def test_auth_routes_contains_key_endpoints(self):
        from mcp_hangar.auth.api.routes import auth_routes

        paths = [r.path for r in auth_routes]
        assert "/keys" in paths
        assert "/roles" in paths
        assert "/principals" in paths
        assert "/permissions" in paths
        assert "/check-permission" in paths

    # --- create_api_key ---

    @pytest.mark.asyncio
    async def test_create_api_key_dispatches_command(self):
        from mcp_hangar.auth.api.routes import create_api_key

        request = self._make_request(body={"principal_id": "user:alice", "name": "my-key"})

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"key_id": "k1", "raw_key": "secret"}
            response = await create_api_key(request)

        assert response.status_code == 201
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:alice"
        assert cmd.name == "my-key"
        assert cmd.created_by == "system"

    @pytest.mark.asyncio
    async def test_create_api_key_with_expires_at(self):
        from mcp_hangar.auth.api.routes import create_api_key

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "name": "temp-key",
                "expires_at": "2026-12-31T23:59:59+00:00",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"key_id": "k2"}
            await create_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.expires_at is not None
        assert cmd.expires_at.year == 2026

    # --- revoke_api_key ---

    @pytest.mark.asyncio
    async def test_revoke_api_key_dispatches_command(self):
        from mcp_hangar.auth.api.routes import revoke_api_key

        request = self._make_request(
            body={"revoked_by": "admin", "reason": "compromised"},
            path_params={"key_id": "k1"},
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            await revoke_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.key_id == "k1"
        assert cmd.revoked_by == "admin"
        assert cmd.reason == "compromised"

    @pytest.mark.asyncio
    async def test_revoke_api_key_handles_empty_body(self):
        from mcp_hangar.auth.api.routes import revoke_api_key

        request = AsyncMock()
        request.path_params = {"key_id": "k2"}
        request.json = AsyncMock(side_effect=json.JSONDecodeError("err", "", 0))

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            await revoke_api_key(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.revoked_by == "system"
        assert cmd.reason == ""

    # --- list_api_keys ---

    @pytest.mark.asyncio
    async def test_list_api_keys_dispatches_query(self):
        from mcp_hangar.auth.api.routes import list_api_keys

        request = self._make_request(query_params={"principal_id": "user:alice"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"keys": [], "total": 0}
            await list_api_keys(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.include_revoked is True

    @pytest.mark.asyncio
    async def test_list_api_keys_include_revoked_false(self):
        from mcp_hangar.auth.api.routes import list_api_keys

        request = self._make_request(query_params={"principal_id": "u", "include_revoked": "false"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"keys": []}
            await list_api_keys(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_revoked is False

    # --- assign_role ---

    @pytest.mark.asyncio
    async def test_assign_role_dispatches_command(self):
        from mcp_hangar.auth.api.routes import assign_role

        request = self._make_request(
            body={
                "principal_id": "user:alice",
                "role_name": "admin",
                "scope": "tenant:x",
                "assigned_by": "superadmin",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"assigned": True}
            await assign_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:alice"
        assert cmd.role_name == "admin"
        assert cmd.scope == "tenant:x"
        assert cmd.assigned_by == "superadmin"

    # --- revoke_role ---

    @pytest.mark.asyncio
    async def test_revoke_role_dispatches_command(self):
        from mcp_hangar.auth.api.routes import revoke_role

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "role_name": "viewer",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"revoked": True}
            await revoke_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.principal_id == "user:bob"
        assert cmd.role_name == "viewer"
        assert cmd.scope == "global"

    # --- list_roles ---

    @pytest.mark.asyncio
    async def test_list_roles_dispatches_query(self):
        from mcp_hangar.auth.api.routes import list_roles

        request = self._make_request()

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            await list_roles(request)

        from mcp_hangar.auth.queries.queries import ListBuiltinRolesQuery

        assert isinstance(mock_dispatch.call_args[0][0], ListBuiltinRolesQuery)

    # --- create_custom_role ---

    @pytest.mark.asyncio
    async def test_create_custom_role_dispatches_command(self):
        from mcp_hangar.auth.api.routes import create_custom_role

        request = self._make_request(
            body={
                "role_name": "deployer",
                "description": "Can deploy",
                "permissions": ["mcp_server:write:*"],
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"role_name": "deployer"}
            response = await create_custom_role(request)

        assert response.status_code == 201
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.role_name == "deployer"
        assert "mcp_server:write:*" in cmd.permissions

    # --- get_principal_roles ---

    @pytest.mark.asyncio
    async def test_get_principal_roles_dispatches_query(self):
        from mcp_hangar.auth.api.routes import get_principal_roles

        request = self._make_request(query_params={"principal_id": "user:alice", "scope": "global"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            await get_principal_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.scope == "global"

    # --- list_all_roles ---

    @pytest.mark.asyncio
    async def test_list_all_roles_dispatches_query(self):
        from mcp_hangar.auth.api.routes import list_all_roles

        request = self._make_request(query_params={})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": [], "total": 0}
            await list_all_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_builtin is True

    @pytest.mark.asyncio
    async def test_list_all_roles_exclude_builtin(self):
        from mcp_hangar.auth.api.routes import list_all_roles

        request = self._make_request(query_params={"include_builtin": "false"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"roles": []}
            await list_all_roles(request)

        query = mock_dispatch.call_args[0][0]
        assert query.include_builtin is False

    # --- get_role ---

    @pytest.mark.asyncio
    async def test_get_role_found(self):
        from mcp_hangar.auth.api.routes import get_role

        request = self._make_request(path_params={"role_name": "admin"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"found": True, "role_name": "admin"}
            response = await get_role(request)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_role_not_found_returns_404(self):
        from mcp_hangar.auth.api.routes import get_role

        request = self._make_request(path_params={"role_name": "nonexistent"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"found": False}
            response = await get_role(request)

        assert response.status_code == 404

    # --- delete_role ---

    @pytest.mark.asyncio
    async def test_delete_role_returns_204(self):
        from mcp_hangar.auth.api.routes import delete_role

        request = self._make_request(path_params={"role_name": "custom-role"})

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await delete_role(request)

        assert response.status_code == 204

    # --- update_role ---

    @pytest.mark.asyncio
    async def test_update_role_dispatches_command(self):
        from mcp_hangar.auth.api.routes import update_role

        request = self._make_request(
            path_params={"role_name": "deployer"},
            body={"permissions": ["mcp_server:write:*"], "description": "Updated", "updated_by": "admin"},
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"role_name": "deployer"}
            await update_role(request)

        cmd = mock_dispatch.call_args[0][0]
        assert cmd.role_name == "deployer"
        assert cmd.permissions == ["mcp_server:write:*"]
        assert cmd.description == "Updated"

    # --- list_principals ---

    @pytest.mark.asyncio
    async def test_list_principals_dispatches_query(self):
        from mcp_hangar.auth.api.routes import list_principals

        request = self._make_request()

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"principals": [], "total": 0}
            await list_principals(request)

        from mcp_hangar.auth.queries.queries import ListPrincipalsQuery

        assert isinstance(mock_dispatch.call_args[0][0], ListPrincipalsQuery)

    # --- list_permissions ---

    @pytest.mark.asyncio
    async def test_list_permissions_returns_permission_manifest(self):
        from mcp_hangar.auth.api.routes import list_permissions

        request = self._make_request()
        response = await list_permissions(request)

        body = json.loads(bytes(response.body))
        assert "permissions" in body
        assert len(body["permissions"]) > 0
        # Each entry should have resource_type and actions
        for perm in body["permissions"]:
            assert "resource_type" in perm
            assert "actions" in perm

    # --- check_permission ---

    @pytest.mark.asyncio
    async def test_check_permission_with_action_fields(self):
        from mcp_hangar.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:alice",
                "action": "invoke",
                "resource_type": "tool",
                "resource_id": "math",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": True}
            await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.principal_id == "user:alice"
        assert query.action == "invoke"
        assert query.resource_type == "tool"
        assert query.resource_id == "math"

    @pytest.mark.asyncio
    async def test_check_permission_with_combined_permission_string(self):
        from mcp_hangar.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "permission": "mcp_server:read:math",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": False}
            await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.resource_type == "mcp_server"
        assert query.action == "read"
        assert query.resource_id == "math"

    @pytest.mark.asyncio
    async def test_check_permission_with_partial_permission_string(self):
        from mcp_hangar.auth.api.routes import check_permission

        request = self._make_request(
            body={
                "principal_id": "user:bob",
                "permission": "mcp_server:read",
            }
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allowed": True}
            await check_permission(request)

        query = mock_dispatch.call_args[0][0]
        assert query.resource_type == "mcp_server"
        assert query.action == "read"
        assert query.resource_id == "*"

    # --- set_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_set_tool_access_policy_valid_scope(self):
        from mcp_hangar.auth.api.routes import set_tool_access_policy

        request = self._make_request(
            path_params={"scope": "provider", "target_id": "math"},
            body={"allow_list": ["add", "subtract"], "deny_list": ["admin_*"]},
        )

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"status": "ok"}
            response = await set_tool_access_policy(request)

        assert response.status_code == 200
        cmd = mock_dispatch.call_args[0][0]
        assert cmd.scope == "provider"
        assert cmd.target_id == "math"
        assert cmd.allow_list == ["add", "subtract"]

    @pytest.mark.asyncio
    async def test_set_tool_access_policy_invalid_scope_returns_400(self):
        from mcp_hangar.auth.api.routes import set_tool_access_policy

        request = self._make_request(
            path_params={"scope": "invalid", "target_id": "x"},
            body={},
        )

        response = await set_tool_access_policy(request)
        assert response.status_code == 400
        body = json.loads(bytes(response.body))
        assert "ValidationError" in body["error"]["code"]

    # --- get_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_get_tool_access_policy_valid_scope(self):
        from mcp_hangar.auth.api.routes import get_tool_access_policy

        request = self._make_request(path_params={"scope": "group", "target_id": "g1"})

        with patch("mcp_hangar.auth.api.routes.dispatch_query", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = {"allow_list": ["*"], "deny_list": []}
            response = await get_tool_access_policy(request)

        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_get_tool_access_policy_invalid_scope_returns_400(self):
        from mcp_hangar.auth.api.routes import get_tool_access_policy

        request = self._make_request(path_params={"scope": "bad", "target_id": "x"})

        response = await get_tool_access_policy(request)
        assert response.status_code == 400

    # --- clear_tool_access_policy ---

    @pytest.mark.asyncio
    async def test_clear_tool_access_policy_returns_204(self):
        from mcp_hangar.auth.api.routes import clear_tool_access_policy

        request = self._make_request(path_params={"scope": "member", "target_id": "m1"})

        with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
            mock_dispatch.return_value = None
            response = await clear_tool_access_policy(request)

        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_clear_tool_access_policy_invalid_scope_returns_400(self):
        from mcp_hangar.auth.api.routes import clear_tool_access_policy

        request = self._make_request(path_params={"scope": "unknown", "target_id": "x"})

        response = await clear_tool_access_policy(request)
        assert response.status_code == 400

    # --- edge cases for TAP scopes ---

    @pytest.mark.asyncio
    async def test_all_valid_tap_scopes_accepted(self):
        from mcp_hangar.auth.api.routes import set_tool_access_policy

        for scope in ("provider", "group", "member"):
            request = self._make_request(
                path_params={"scope": scope, "target_id": "t1"},
                body={"allow_list": ["*"]},
            )

            with patch("mcp_hangar.auth.api.routes.dispatch_command", new_callable=AsyncMock) as mock_dispatch:
                mock_dispatch.return_value = {"ok": True}
                response = await set_tool_access_policy(request)
                assert response.status_code == 200, f"Scope {scope} should be accepted"
