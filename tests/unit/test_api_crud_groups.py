"""Tests for group CRUD REST API endpoints.

Tests cover:
- POST /groups/ (create)
- PUT /groups/{group_id} (update)
- DELETE /groups/{group_id} (delete)
- POST /groups/{group_id}/members (add member)
- DELETE /groups/{group_id}/members/{member_id} (remove member)
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import (
    AddGroupMemberCommand,
    CreateGroupCommand,
    DeleteGroupCommand,
    RemoveGroupMemberCommand,
    UpdateGroupCommand,
)
from mcp_hangar.domain.exceptions import ProviderNotFoundError, ValidationError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_context():
    """Mock ApplicationContext with command bus."""
    ctx = Mock()
    command_bus = Mock()
    query_bus = Mock()

    def send_command(command):
        if isinstance(command, CreateGroupCommand):
            if command.group_id == "new-group":
                return {"group_id": "new-group", "created": True}
            raise ValidationError(f"Group already exists: {command.group_id}")
        elif isinstance(command, UpdateGroupCommand):
            if command.group_id == "my-group":
                return {"group_id": "my-group", "updated": True}
            raise ProviderNotFoundError(command.group_id)
        elif isinstance(command, DeleteGroupCommand):
            if command.group_id == "my-group":
                return {"group_id": "my-group", "deleted": True}
            raise ProviderNotFoundError(command.group_id)
        elif isinstance(command, AddGroupMemberCommand):
            if command.group_id == "my-group":
                return {"group_id": "my-group", "mcp_server_id": command.mcp_server_id, "added": True}
            raise ProviderNotFoundError(command.group_id)
        elif isinstance(command, RemoveGroupMemberCommand):
            if command.group_id == "my-group":
                return {"group_id": "my-group", "mcp_server_id": command.mcp_server_id, "removed": True}
            raise ProviderNotFoundError(command.group_id)
        raise ValueError(f"Unexpected command: {type(command)}")

    command_bus.send.side_effect = send_command
    ctx.command_bus = command_bus
    ctx.query_bus = query_bus
    # groups needed for existing list/get endpoints
    ctx.groups = {}
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient for the API app with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.groups.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# POST /groups/
# ---------------------------------------------------------------------------


class TestCreateGroup:
    """Tests for POST /groups/ endpoint."""

    def test_returns_201_on_success(self, api_client):
        """POST /groups/ returns HTTP 201 on creation."""
        response = api_client.post(
            "/groups/",
            json={"group_id": "new-group", "strategy": "round_robin"},
        )
        assert response.status_code == 201

    def test_returns_created_flag(self, api_client):
        """POST /groups/ returns created=True in body."""
        response = api_client.post(
            "/groups/",
            json={"group_id": "new-group", "strategy": "round_robin"},
        )
        data = response.json()
        assert data["group_id"] == "new-group"
        assert data["created"] is True

    def test_returns_422_on_duplicate(self, api_client):
        """POST /groups/ returns 422 when group already exists."""
        response = api_client.post(
            "/groups/",
            json={"group_id": "existing-group", "strategy": "round_robin"},
        )
        assert response.status_code == 422

    def test_dispatches_create_group_command(self, api_client, mock_context):
        """POST /groups/ dispatches CreateGroupCommand."""
        api_client.post("/groups/", json={"group_id": "new-group", "strategy": "round_robin"})
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], CreateGroupCommand) for call in calls)

    def test_command_includes_all_fields(self, api_client, mock_context):
        """POST /groups/ dispatches command with correct fields."""
        api_client.post(
            "/groups/",
            json={"group_id": "new-group", "strategy": "round_robin", "min_healthy": 2, "description": "test"},
        )
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], CreateGroupCommand))
        assert cmd.group_id == "new-group"
        assert cmd.strategy == "round_robin"
        assert cmd.min_healthy == 2
        assert cmd.description == "test"


# ---------------------------------------------------------------------------
# PUT /groups/{group_id}
# ---------------------------------------------------------------------------


class TestUpdateGroup:
    """Tests for PUT /groups/{group_id} endpoint."""

    def test_returns_200_on_success(self, api_client):
        """PUT /groups/my-group returns HTTP 200."""
        response = api_client.put("/groups/my-group", json={"description": "Updated"})
        assert response.status_code == 200

    def test_returns_updated_flag(self, api_client):
        """PUT /groups/my-group returns updated=True in body."""
        response = api_client.put("/groups/my-group", json={"description": "Updated"})
        data = response.json()
        assert data["group_id"] == "my-group"
        assert data["updated"] is True

    def test_returns_404_for_unknown_group(self, api_client):
        """PUT /groups/unknown returns HTTP 404."""
        response = api_client.put("/groups/unknown", json={"description": "test"})
        assert response.status_code == 404

    def test_dispatches_update_group_command(self, api_client, mock_context):
        """PUT /groups/my-group dispatches UpdateGroupCommand."""
        api_client.put("/groups/my-group", json={"description": "Updated"})
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], UpdateGroupCommand) for call in calls)

    def test_command_includes_group_id_from_path(self, api_client, mock_context):
        """PUT /groups/my-group dispatches command with group_id from path."""
        api_client.put("/groups/my-group", json={"description": "Updated"})
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], UpdateGroupCommand))
        assert cmd.group_id == "my-group"


# ---------------------------------------------------------------------------
# DELETE /groups/{group_id}
# ---------------------------------------------------------------------------


class TestDeleteGroup:
    """Tests for DELETE /groups/{group_id} endpoint."""

    def test_returns_200_on_success(self, api_client):
        """DELETE /groups/my-group returns HTTP 200."""
        response = api_client.delete("/groups/my-group")
        assert response.status_code == 200

    def test_returns_deleted_flag(self, api_client):
        """DELETE /groups/my-group returns deleted=True in body."""
        response = api_client.delete("/groups/my-group")
        data = response.json()
        assert data["group_id"] == "my-group"
        assert data["deleted"] is True

    def test_returns_404_for_unknown_group(self, api_client):
        """DELETE /groups/unknown returns HTTP 404."""
        response = api_client.delete("/groups/unknown")
        assert response.status_code == 404

    def test_dispatches_delete_group_command(self, api_client, mock_context):
        """DELETE /groups/my-group dispatches DeleteGroupCommand."""
        api_client.delete("/groups/my-group")
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], DeleteGroupCommand) for call in calls)


# ---------------------------------------------------------------------------
# POST /groups/{group_id}/members
# ---------------------------------------------------------------------------


class TestAddGroupMember:
    """Tests for POST /groups/{group_id}/members endpoint."""

    def test_returns_201_on_success(self, api_client):
        """POST /groups/my-group/members returns HTTP 201."""
        response = api_client.post(
            "/groups/my-group/members",
            json={"member_id": "math", "weight": 1, "priority": 1},
        )
        assert response.status_code == 201

    def test_returns_added_flag(self, api_client):
        """POST /groups/my-group/members returns added=True in body."""
        response = api_client.post(
            "/groups/my-group/members",
            json={"member_id": "math", "weight": 1, "priority": 1},
        )
        data = response.json()
        assert data["group_id"] == "my-group"
        assert data["added"] is True

    def test_returns_404_for_unknown_group(self, api_client):
        """POST /groups/unknown/members returns HTTP 404."""
        response = api_client.post(
            "/groups/unknown/members",
            json={"member_id": "math"},
        )
        assert response.status_code == 404

    def test_dispatches_add_group_member_command(self, api_client, mock_context):
        """POST /groups/my-group/members dispatches AddGroupMemberCommand."""
        api_client.post("/groups/my-group/members", json={"member_id": "math"})
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], AddGroupMemberCommand) for call in calls)

    def test_command_uses_member_id_as_mcp_server_id(self, api_client, mock_context):
        """POST /groups/my-group/members maps member_id to mcp_server_id in command."""
        api_client.post("/groups/my-group/members", json={"member_id": "math", "weight": 2})
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], AddGroupMemberCommand))
        assert cmd.mcp_server_id == "math"
        assert cmd.group_id == "my-group"
        assert cmd.weight == 2


# ---------------------------------------------------------------------------
# DELETE /groups/{group_id}/members/{member_id}
# ---------------------------------------------------------------------------


class TestRemoveGroupMember:
    """Tests for DELETE /groups/{group_id}/members/{member_id} endpoint."""

    def test_returns_200_on_success(self, api_client):
        """DELETE /groups/my-group/members/math returns HTTP 200."""
        response = api_client.delete("/groups/my-group/members/math")
        assert response.status_code == 200

    def test_returns_removed_flag(self, api_client):
        """DELETE /groups/my-group/members/math returns removed=True in body."""
        response = api_client.delete("/groups/my-group/members/math")
        data = response.json()
        assert data["group_id"] == "my-group"
        assert data["removed"] is True

    def test_returns_404_for_unknown_group(self, api_client):
        """DELETE /groups/unknown/members/math returns HTTP 404."""
        response = api_client.delete("/groups/unknown/members/math")
        assert response.status_code == 404

    def test_dispatches_remove_group_member_command(self, api_client, mock_context):
        """DELETE /groups/my-group/members/math dispatches RemoveGroupMemberCommand."""
        api_client.delete("/groups/my-group/members/math")
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], RemoveGroupMemberCommand) for call in calls)

    def test_command_uses_path_params(self, api_client, mock_context):
        """DELETE /groups/my-group/members/math dispatches command with path params."""
        api_client.delete("/groups/my-group/members/math")
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], RemoveGroupMemberCommand))
        assert cmd.group_id == "my-group"
        assert cmd.mcp_server_id == "math"
