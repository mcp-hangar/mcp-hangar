"""Tests for auth REST API endpoints.

Tests cover:
- POST /auth/keys - Create API key (returns 201 with raw_key)
- DELETE /auth/keys/{key_id} - Revoke API key (returns 200)
- GET /auth/keys?principal_id=X - List API keys (returns 200)
- POST /auth/roles/assign - Assign role to principal (returns 200)
- DELETE /auth/roles/revoke - Revoke role from principal (returns 200)
- GET /auth/roles - List built-in roles (returns 200)
- POST /auth/roles - Create custom role (returns 201)
- GET /auth/principals/roles?principal_id=X - Get roles for principal (returns 200)
"""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_context():
    """Mock ApplicationContext with command bus and query bus."""
    from unittest.mock import Mock

    ctx = Mock()
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient for the auth API app with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


# ---------------------------------------------------------------------------
# POST /auth/keys
# ---------------------------------------------------------------------------


class TestCreateApiKey:
    """Tests for POST /auth/keys."""

    def test_returns_201(self, api_client):
        """POST /auth/keys returns HTTP 201."""
        mock_result = {
            "key_id": "key-001",
            "raw_key": "mcp-sk-abc123",
            "principal_id": "alice",
            "name": "my-key",
            "warning": "Save this key now - it cannot be retrieved later!",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/keys",
                json={"principal_id": "alice", "name": "my-key"},
            )
        assert response.status_code == 201

    def test_returns_raw_key_in_response(self, api_client):
        """POST /auth/keys returns raw_key in body."""
        mock_result = {
            "key_id": "key-001",
            "raw_key": "mcp-sk-abc123",
            "principal_id": "alice",
            "name": "my-key",
            "warning": "Save this key now - it cannot be retrieved later!",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/keys",
                json={"principal_id": "alice", "name": "my-key"},
            )
        data = response.json()
        assert data["raw_key"] == "mcp-sk-abc123"
        assert data["key_id"] == "key-001"
        assert data["principal_id"] == "alice"

    def test_passes_correct_command(self, api_client):
        """POST /auth/keys dispatches CreateApiKeyCommand with correct fields."""
        from enterprise.auth.commands.commands import CreateApiKeyCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {"key_id": "k1", "raw_key": "sk-test", "principal_id": "bob", "name": "test"}

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            response = api_client.post(
                "/auth/keys",
                json={"principal_id": "bob", "name": "test-key", "created_by": "admin"},
            )

        assert response.status_code == 201
        assert len(captured) == 1
        cmd = captured[0]
        assert isinstance(cmd, CreateApiKeyCommand)
        assert cmd.principal_id == "bob"
        assert cmd.name == "test-key"
        assert cmd.created_by == "admin"


# ---------------------------------------------------------------------------
# DELETE /auth/keys/{key_id}
# ---------------------------------------------------------------------------


class TestRevokeApiKey:
    """Tests for DELETE /auth/keys/{key_id}."""

    def test_returns_200(self, api_client):
        """DELETE /auth/keys/{key_id} returns HTTP 200."""
        mock_result = {"key_id": "key-001", "revoked": True, "revoked_by": "admin", "reason": ""}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.delete("/auth/keys/key-001")
        assert response.status_code == 200

    def test_returns_revoked_true(self, api_client):
        """DELETE /auth/keys/{key_id} returns revoked=true in body."""
        mock_result = {"key_id": "key-001", "revoked": True, "revoked_by": "system", "reason": ""}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.delete("/auth/keys/key-001")
        data = response.json()
        assert data["revoked"] is True
        assert data["key_id"] == "key-001"

    def test_passes_key_id_from_path(self, api_client):
        """DELETE /auth/keys/{key_id} dispatches RevokeApiKeyCommand with key_id from path."""
        from enterprise.auth.commands.commands import RevokeApiKeyCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {"key_id": "key-xyz", "revoked": True, "revoked_by": "system", "reason": ""}

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            response = api_client.delete("/auth/keys/key-xyz")

        assert response.status_code == 200
        assert len(captured) == 1
        cmd = captured[0]
        assert isinstance(cmd, RevokeApiKeyCommand)
        assert cmd.key_id == "key-xyz"


# ---------------------------------------------------------------------------
# GET /auth/keys
# ---------------------------------------------------------------------------


class TestListApiKeys:
    """Tests for GET /auth/keys."""

    def test_returns_200(self, api_client):
        """GET /auth/keys?principal_id=alice returns HTTP 200."""
        mock_result = {
            "principal_id": "alice",
            "keys": [],
            "total": 0,
            "active": 0,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/keys?principal_id=alice")
        assert response.status_code == 200

    def test_returns_keys_list(self, api_client):
        """GET /auth/keys?principal_id=alice returns keys list."""
        mock_result = {
            "principal_id": "alice",
            "keys": [
                {
                    "key_id": "k1",
                    "name": "prod-key",
                    "created_at": None,
                    "expires_at": None,
                    "last_used_at": None,
                    "revoked": False,
                }
            ],
            "total": 1,
            "active": 1,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/keys?principal_id=alice")
        data = response.json()
        assert data["principal_id"] == "alice"
        assert data["total"] == 1
        assert len(data["keys"]) == 1

    def test_passes_correct_query(self, api_client):
        """GET /auth/keys dispatches GetApiKeysByPrincipalQuery with correct params."""
        from enterprise.auth.queries.queries import GetApiKeysByPrincipalQuery

        captured = []

        async def capture_query(q):
            captured.append(q)
            return {"principal_id": "bob", "keys": [], "total": 0, "active": 0}

        with patch("mcp_hangar.server.api.auth.dispatch_query", side_effect=capture_query):
            response = api_client.get("/auth/keys?principal_id=bob&include_revoked=false")

        assert response.status_code == 200
        assert len(captured) == 1
        q = captured[0]
        assert isinstance(q, GetApiKeysByPrincipalQuery)
        assert q.principal_id == "bob"
        assert q.include_revoked is False


# ---------------------------------------------------------------------------
# POST /auth/roles/assign
# ---------------------------------------------------------------------------


class TestAssignRole:
    """Tests for POST /auth/roles/assign."""

    def test_returns_200(self, api_client):
        """POST /auth/roles/assign returns HTTP 200."""
        mock_result = {
            "principal_id": "alice",
            "role_name": "viewer",
            "scope": "global",
            "assigned": True,
            "assigned_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/roles/assign",
                json={"principal_id": "alice", "role_name": "viewer"},
            )
        assert response.status_code == 200

    def test_returns_assigned_true(self, api_client):
        """POST /auth/roles/assign returns assigned=true in body."""
        mock_result = {
            "principal_id": "alice",
            "role_name": "viewer",
            "scope": "global",
            "assigned": True,
            "assigned_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/roles/assign",
                json={"principal_id": "alice", "role_name": "viewer"},
            )
        data = response.json()
        assert data["assigned"] is True
        assert data["principal_id"] == "alice"
        assert data["role_name"] == "viewer"

    def test_passes_correct_command(self, api_client):
        """POST /auth/roles/assign dispatches AssignRoleCommand with correct fields."""
        from enterprise.auth.commands.commands import AssignRoleCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {
                "principal_id": "bob",
                "role_name": "admin",
                "scope": "global",
                "assigned": True,
                "assigned_by": "root",
            }

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            response = api_client.post(
                "/auth/roles/assign",
                json={"principal_id": "bob", "role_name": "admin", "scope": "global", "assigned_by": "root"},
            )

        assert response.status_code == 200
        cmd = captured[0]
        assert isinstance(cmd, AssignRoleCommand)
        assert cmd.principal_id == "bob"
        assert cmd.role_name == "admin"
        assert cmd.scope == "global"
        assert cmd.assigned_by == "root"


# ---------------------------------------------------------------------------
# DELETE /auth/roles/revoke
# ---------------------------------------------------------------------------


class TestRevokeRole:
    """Tests for DELETE /auth/roles/revoke."""

    def test_returns_200(self, api_client):
        """DELETE /auth/roles/revoke returns HTTP 200."""
        mock_result = {
            "principal_id": "alice",
            "role_name": "viewer",
            "scope": "global",
            "revoked": True,
            "revoked_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.request(
                "DELETE",
                "/auth/roles/revoke",
                json={"principal_id": "alice", "role_name": "viewer"},
            )
        assert response.status_code == 200

    def test_returns_revoked_true(self, api_client):
        """DELETE /auth/roles/revoke returns revoked=true in body."""
        mock_result = {
            "principal_id": "alice",
            "role_name": "viewer",
            "scope": "global",
            "revoked": True,
            "revoked_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.request(
                "DELETE",
                "/auth/roles/revoke",
                json={"principal_id": "alice", "role_name": "viewer"},
            )
        data = response.json()
        assert data["revoked"] is True
        assert data["principal_id"] == "alice"


# ---------------------------------------------------------------------------
# GET /auth/roles
# ---------------------------------------------------------------------------


class TestListBuiltinRoles:
    """Tests for GET /auth/roles."""

    def test_returns_200(self, api_client):
        """GET /auth/roles returns HTTP 200."""
        mock_result = {
            "roles": [
                {"name": "admin", "description": "Full access", "permissions_count": 5},
                {"name": "viewer", "description": "Read-only", "permissions_count": 2},
            ],
            "count": 2,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles")
        assert response.status_code == 200

    def test_returns_roles_list(self, api_client):
        """GET /auth/roles returns list of roles."""
        mock_result = {
            "roles": [
                {"name": "admin", "description": "Full access", "permissions_count": 5},
                {"name": "viewer", "description": "Read-only", "permissions_count": 2},
            ],
            "count": 2,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles")
        data = response.json()
        assert "roles" in data
        assert isinstance(data["roles"], list)
        assert data["count"] == 2

    def test_dispatches_list_builtin_roles_query(self, api_client):
        """GET /auth/roles dispatches ListBuiltinRolesQuery."""
        from enterprise.auth.queries.queries import ListBuiltinRolesQuery

        captured = []

        async def capture_query(q):
            captured.append(q)
            return {"roles": [], "count": 0}

        with patch("mcp_hangar.server.api.auth.dispatch_query", side_effect=capture_query):
            response = api_client.get("/auth/roles")

        assert response.status_code == 200
        assert len(captured) == 1
        assert isinstance(captured[0], ListBuiltinRolesQuery)


# ---------------------------------------------------------------------------
# POST /auth/roles (custom role)
# ---------------------------------------------------------------------------


class TestCreateCustomRole:
    """Tests for POST /auth/roles."""

    def test_returns_201(self, api_client):
        """POST /auth/roles returns HTTP 201."""
        mock_result = {
            "role_name": "custom-role",
            "description": "Custom role",
            "permissions_count": 1,
            "created": True,
            "created_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/roles",
                json={"role_name": "custom-role", "description": "Custom role"},
            )
        assert response.status_code == 201

    def test_returns_created_true(self, api_client):
        """POST /auth/roles returns created=true in body."""
        mock_result = {
            "role_name": "custom-role",
            "description": "Custom role",
            "permissions_count": 0,
            "created": True,
            "created_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/roles",
                json={"role_name": "custom-role"},
            )
        data = response.json()
        assert data["created"] is True
        assert data["role_name"] == "custom-role"

    def test_passes_correct_command(self, api_client):
        """POST /auth/roles dispatches CreateCustomRoleCommand with correct fields."""
        from enterprise.auth.commands.commands import CreateCustomRoleCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {
                "role_name": "ops",
                "description": "Ops role",
                "permissions_count": 2,
                "created": True,
                "created_by": "system",
            }

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            response = api_client.post(
                "/auth/roles",
                json={
                    "role_name": "ops",
                    "description": "Ops role",
                    "permissions": ["providers:start:*", "providers:stop:*"],
                    "created_by": "system",
                },
            )

        assert response.status_code == 201
        cmd = captured[0]
        assert isinstance(cmd, CreateCustomRoleCommand)
        assert cmd.role_name == "ops"
        assert cmd.description == "Ops role"
        assert "providers:start:*" in cmd.permissions
        assert "providers:stop:*" in cmd.permissions


# ---------------------------------------------------------------------------
# GET /auth/principals/roles
# ---------------------------------------------------------------------------


class TestGetPrincipalRoles:
    """Tests for GET /auth/principals/roles."""

    def test_returns_200(self, api_client):
        """GET /auth/principals/roles?principal_id=alice returns HTTP 200."""
        mock_result = {
            "principal_id": "alice",
            "scope": "*",
            "roles": [],
            "count": 0,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/principals/roles?principal_id=alice")
        assert response.status_code == 200

    def test_returns_roles_for_principal(self, api_client):
        """GET /auth/principals/roles returns roles list for principal."""
        mock_result = {
            "principal_id": "alice",
            "scope": "*",
            "roles": [
                {"name": "viewer", "description": "Read-only", "permissions": []},
            ],
            "count": 1,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/principals/roles?principal_id=alice")
        data = response.json()
        assert data["principal_id"] == "alice"
        assert data["count"] == 1
        assert len(data["roles"]) == 1


# ---------------------------------------------------------------------------
# GET /auth/roles/all
# ---------------------------------------------------------------------------


class TestListAllRoles:
    """Tests for GET /auth/roles/all."""

    def test_returns_200(self, api_client):
        """GET /auth/roles/all returns HTTP 200."""
        mock_result = {"roles": [], "total": 0, "builtin_count": 0, "custom_count": 0}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles/all")
        assert response.status_code == 200

    def test_returns_roles_list(self, api_client):
        """GET /auth/roles/all returns roles list with total."""
        mock_result = {
            "roles": [{"name": "admin", "is_builtin": True, "permissions_count": 5}],
            "total": 1,
            "builtin_count": 1,
            "custom_count": 0,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles/all")
        data = response.json()
        assert "roles" in data
        assert data["total"] == 1

    def test_dispatches_list_all_roles_query(self, api_client):
        """GET /auth/roles/all dispatches ListAllRolesQuery."""
        from enterprise.auth.queries.queries import ListAllRolesQuery

        captured = []

        async def capture_query(q):
            captured.append(q)
            return {"roles": [], "total": 0, "builtin_count": 0, "custom_count": 0}

        with patch("mcp_hangar.server.api.auth.dispatch_query", side_effect=capture_query):
            api_client.get("/auth/roles/all?include_builtin=false")

        assert len(captured) == 1
        assert isinstance(captured[0], ListAllRolesQuery)
        assert captured[0].include_builtin is False


# ---------------------------------------------------------------------------
# GET /auth/roles/{role_name}
# ---------------------------------------------------------------------------


class TestGetRole:
    """Tests for GET /auth/roles/{role_name}."""

    def test_returns_200_when_found(self, api_client):
        """GET /auth/roles/{role_name} returns 200 when role exists."""
        mock_result = {
            "role_name": "admin",
            "description": "Full access",
            "permissions": [],
            "found": True,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles/admin")
        assert response.status_code == 200

    def test_returns_role_data(self, api_client):
        """GET /auth/roles/{role_name} returns role data in body."""
        mock_result = {
            "role_name": "developer",
            "description": "Developer role",
            "permissions": ["tool:invoke:*"],
            "found": True,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/roles/developer")
        data = response.json()
        assert data["role_name"] == "developer"


# ---------------------------------------------------------------------------
# DELETE /auth/roles/{role_name}
# ---------------------------------------------------------------------------


class TestDeleteRole:
    """Tests for DELETE /auth/roles/{role_name}."""

    def test_returns_204(self, api_client):
        """DELETE /auth/roles/{role_name} returns HTTP 204."""
        mock_result = {"role_name": "my-role", "deleted": True, "deleted_by": "system"}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.delete("/auth/roles/my-role")
        assert response.status_code == 204

    def test_passes_role_name_from_path(self, api_client):
        """DELETE /auth/roles/{role_name} dispatches DeleteCustomRoleCommand with correct role_name."""
        from enterprise.auth.commands.commands import DeleteCustomRoleCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {"role_name": "ops-role", "deleted": True, "deleted_by": "system"}

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            api_client.delete("/auth/roles/ops-role")

        assert len(captured) == 1
        assert isinstance(captured[0], DeleteCustomRoleCommand)
        assert captured[0].role_name == "ops-role"


# ---------------------------------------------------------------------------
# PATCH /auth/roles/{role_name}
# ---------------------------------------------------------------------------


class TestUpdateRole:
    """Tests for PATCH /auth/roles/{role_name}."""

    def test_returns_200(self, api_client):
        """PATCH /auth/roles/{role_name} returns HTTP 200."""
        mock_result = {
            "role_name": "my-role",
            "description": "Updated",
            "permissions_count": 1,
            "updated": True,
            "updated_by": "system",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.patch(
                "/auth/roles/my-role",
                json={"permissions": ["tool:invoke:*"], "description": "Updated"},
            )
        assert response.status_code == 200

    def test_returns_updated_role(self, api_client):
        """PATCH /auth/roles/{role_name} returns updated role in body."""
        mock_result = {
            "role_name": "my-role",
            "description": "New desc",
            "permissions_count": 2,
            "updated": True,
            "updated_by": "admin",
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.patch("/auth/roles/my-role", json={"permissions": []})
        data = response.json()
        assert data["updated"] is True
        assert data["role_name"] == "my-role"


# ---------------------------------------------------------------------------
# GET /auth/principals
# ---------------------------------------------------------------------------


class TestListPrincipals:
    """Tests for GET /auth/principals."""

    def test_returns_200(self, api_client):
        """GET /auth/principals returns HTTP 200."""
        mock_result = {"principals": [], "total": 0}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/principals")
        assert response.status_code == 200

    def test_returns_principals_list(self, api_client):
        """GET /auth/principals returns principals list."""
        mock_result = {
            "principals": [{"principal_id": "alice", "roles": ["viewer"]}],
            "total": 1,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/principals")
        data = response.json()
        assert data["total"] == 1
        assert len(data["principals"]) == 1


# ---------------------------------------------------------------------------
# GET /auth/permissions
# ---------------------------------------------------------------------------


class TestListPermissions:
    """Tests for GET /auth/permissions."""

    def test_returns_200(self, api_client):
        """GET /auth/permissions returns HTTP 200 with static list."""
        response = api_client.get("/auth/permissions")
        assert response.status_code == 200

    def test_returns_non_empty_permissions(self, api_client):
        """GET /auth/permissions returns a non-empty list."""
        response = api_client.get("/auth/permissions")
        data = response.json()
        assert "permissions" in data
        assert len(data["permissions"]) > 0


# ---------------------------------------------------------------------------
# POST /auth/check-permission
# ---------------------------------------------------------------------------


class TestCheckPermission:
    """Tests for POST /auth/check-permission."""

    def test_returns_200(self, api_client):
        """POST /auth/check-permission returns HTTP 200."""
        mock_result = {"allowed": True, "principal_id": "alice", "permission": "tool:invoke:math"}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/check-permission",
                json={"principal_id": "alice", "permission": "tool:invoke:math"},
            )
        assert response.status_code == 200

    def test_returns_allowed_bool(self, api_client):
        """POST /auth/check-permission returns allowed field."""
        mock_result = {"allowed": False, "principal_id": "bob", "permission": "tool:invoke:admin_tool"}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/check-permission",
                json={"principal_id": "bob", "permission": "tool:invoke:admin_tool"},
            )
        data = response.json()
        assert "allowed" in data


# ---------------------------------------------------------------------------
# POST /auth/policies/{scope}/{target_id}
# ---------------------------------------------------------------------------


class TestSetToolAccessPolicy:
    """Tests for POST /auth/policies/{scope}/{target_id}."""

    def test_returns_200_for_provider_scope(self, api_client):
        """POST /auth/policies/provider/{id} returns HTTP 200."""
        mock_result = {
            "scope": "provider",
            "target_id": "math",
            "allow_list": ["add"],
            "deny_list": [],
            "set": True,
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.post(
                "/auth/policies/provider/math",
                json={"allow_list": ["add"], "deny_list": []},
            )
        assert response.status_code == 200

    def test_returns_400_for_invalid_scope(self, api_client):
        """POST /auth/policies/{invalid_scope}/id returns HTTP 400."""
        response = api_client.post(
            "/auth/policies/invalid_scope/math",
            json={"allow_list": [], "deny_list": []},
        )
        assert response.status_code == 400

    def test_dispatches_set_tap_command(self, api_client):
        """POST /auth/policies/{scope}/{id} dispatches SetToolAccessPolicyCommand."""
        from enterprise.auth.commands.commands import SetToolAccessPolicyCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {"scope": "group", "target_id": "team-a", "allow_list": ["tool1"], "deny_list": [], "set": True}

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            api_client.post(
                "/auth/policies/group/team-a",
                json={"allow_list": ["tool1"], "deny_list": []},
            )

        assert len(captured) == 1
        assert isinstance(captured[0], SetToolAccessPolicyCommand)
        assert captured[0].scope == "group"
        assert captured[0].target_id == "team-a"


# ---------------------------------------------------------------------------
# GET /auth/policies/{scope}/{target_id}
# ---------------------------------------------------------------------------


class TestGetToolAccessPolicy:
    """Tests for GET /auth/policies/{scope}/{target_id}."""

    def test_returns_200(self, api_client):
        """GET /auth/policies/{scope}/{id} returns HTTP 200."""
        mock_result = {
            "found": False,
            "scope": "provider",
            "target_id": "math",
            "allow_list": [],
            "deny_list": [],
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/policies/provider/math")
        assert response.status_code == 200

    def test_returns_found_field(self, api_client):
        """GET /auth/policies/{scope}/{id} returns found field in body."""
        mock_result = {
            "found": True,
            "scope": "provider",
            "target_id": "math",
            "allow_list": ["add"],
            "deny_list": [],
        }
        with patch(
            "mcp_hangar.server.api.auth.dispatch_query",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.get("/auth/policies/provider/math")
        data = response.json()
        assert "found" in data
        assert data["found"] is True


# ---------------------------------------------------------------------------
# DELETE /auth/policies/{scope}/{target_id}
# ---------------------------------------------------------------------------


class TestClearToolAccessPolicy:
    """Tests for DELETE /auth/policies/{scope}/{target_id}."""

    def test_returns_204(self, api_client):
        """DELETE /auth/policies/{scope}/{id} returns HTTP 204."""
        mock_result = {"scope": "provider", "target_id": "math", "cleared": True}
        with patch(
            "mcp_hangar.server.api.auth.dispatch_command",
            new=AsyncMock(return_value=mock_result),
        ):
            response = api_client.delete("/auth/policies/provider/math")
        assert response.status_code == 204

    def test_dispatches_clear_tap_command(self, api_client):
        """DELETE /auth/policies/{scope}/{id} dispatches ClearToolAccessPolicyCommand."""
        from enterprise.auth.commands.commands import ClearToolAccessPolicyCommand

        captured = []

        async def capture_command(cmd):
            captured.append(cmd)
            return {"scope": "provider", "target_id": "math", "cleared": True}

        with patch("mcp_hangar.server.api.auth.dispatch_command", side_effect=capture_command):
            api_client.delete("/auth/policies/provider/math")

        assert len(captured) == 1
        assert isinstance(captured[0], ClearToolAccessPolicyCommand)
        assert captured[0].scope == "provider"
        assert captured[0].target_id == "math"
