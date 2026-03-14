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
        from mcp_hangar.application.commands.auth_commands import CreateApiKeyCommand

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
        from mcp_hangar.application.commands.auth_commands import RevokeApiKeyCommand

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
        from mcp_hangar.application.queries.auth_queries import GetApiKeysByPrincipalQuery

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
        from mcp_hangar.application.commands.auth_commands import AssignRoleCommand

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
        from mcp_hangar.application.queries.auth_queries import ListBuiltinRolesQuery

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
        from mcp_hangar.application.commands.auth_commands import CreateCustomRoleCommand

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
