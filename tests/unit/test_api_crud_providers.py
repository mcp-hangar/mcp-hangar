"""Tests for provider CRUD REST API endpoints.

Tests cover:
- POST /providers/ (create)
- PUT /providers/{id} (update)
- DELETE /providers/{id} (delete)
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.crud_commands import (
    CreateProviderCommand,
    DeleteProviderCommand,
    UpdateProviderCommand,
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
        if isinstance(command, CreateProviderCommand):
            if command.provider_id == "new-provider":
                return {"provider_id": "new-provider", "created": True}
            raise ValidationError(f"Provider already exists: {command.provider_id}")
        elif isinstance(command, UpdateProviderCommand):
            if command.provider_id == "math":
                return {"provider_id": "math", "updated": True}
            raise ProviderNotFoundError(command.provider_id)
        elif isinstance(command, DeleteProviderCommand):
            if command.provider_id == "math":
                return {"provider_id": "math", "deleted": True}
            raise ProviderNotFoundError(command.provider_id)
        raise ValueError(f"Unexpected command: {type(command)}")

    command_bus.send.side_effect = send_command
    ctx.command_bus = command_bus
    ctx.query_bus = query_bus
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient for the API app with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


# ---------------------------------------------------------------------------
# POST /providers/
# ---------------------------------------------------------------------------


class TestCreateProvider:
    """Tests for POST /providers/ endpoint."""

    def test_returns_201_on_success(self, api_client):
        """POST /providers/ returns HTTP 201 on creation."""
        response = api_client.post(
            "/providers/",
            json={"provider_id": "new-provider", "mode": "subprocess", "command": ["python", "-m", "srv"]},
        )
        assert response.status_code == 201

    def test_returns_created_flag(self, api_client):
        """POST /providers/ returns created=True in body."""
        response = api_client.post(
            "/providers/",
            json={"provider_id": "new-provider", "mode": "subprocess", "command": ["python", "-m", "srv"]},
        )
        data = response.json()
        assert data["provider_id"] == "new-provider"
        assert data["created"] is True

    def test_returns_422_on_duplicate(self, api_client):
        """POST /providers/ returns 422 when provider_id already exists."""
        response = api_client.post(
            "/providers/",
            json={"provider_id": "existing-provider", "mode": "subprocess", "command": ["python", "-m", "srv"]},
        )
        assert response.status_code == 422

    def test_dispatches_create_provider_command(self, api_client, mock_context):
        """POST /providers/ dispatches CreateProviderCommand."""
        api_client.post(
            "/providers/",
            json={"provider_id": "new-provider", "mode": "subprocess", "command": ["python", "-m", "srv"]},
        )
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], CreateProviderCommand) for call in calls)

    def test_command_includes_all_required_fields(self, api_client, mock_context):
        """POST /providers/ dispatches command with correct fields."""
        api_client.post(
            "/providers/",
            json={
                "provider_id": "new-provider",
                "mode": "subprocess",
                "command": ["python", "-m", "srv"],
                "description": "my provider",
                "idle_ttl_s": 600,
            },
        )
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], CreateProviderCommand))
        assert cmd.provider_id == "new-provider"
        assert cmd.mode == "subprocess"
        assert cmd.description == "my provider"
        assert cmd.idle_ttl_s == 600


# ---------------------------------------------------------------------------
# PUT /providers/{provider_id}
# ---------------------------------------------------------------------------


class TestUpdateProvider:
    """Tests for PUT /providers/{provider_id} endpoint."""

    def test_returns_200_on_success(self, api_client):
        """PUT /providers/math returns HTTP 200."""
        response = api_client.put("/providers/math", json={"description": "Updated description"})
        assert response.status_code == 200

    def test_returns_updated_flag(self, api_client):
        """PUT /providers/math returns updated=True in body."""
        response = api_client.put("/providers/math", json={"description": "Updated description"})
        data = response.json()
        assert data["provider_id"] == "math"
        assert data["updated"] is True

    def test_returns_404_for_unknown_provider(self, api_client):
        """PUT /providers/unknown returns HTTP 404."""
        response = api_client.put("/providers/unknown", json={"description": "test"})
        assert response.status_code == 404

    def test_dispatches_update_provider_command(self, api_client, mock_context):
        """PUT /providers/math dispatches UpdateProviderCommand."""
        api_client.put("/providers/math", json={"description": "Updated"})
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], UpdateProviderCommand) for call in calls)

    def test_command_includes_provider_id_from_path(self, api_client, mock_context):
        """PUT /providers/math dispatches command with provider_id from path."""
        api_client.put("/providers/math", json={"description": "Updated"})
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], UpdateProviderCommand))
        assert cmd.provider_id == "math"


# ---------------------------------------------------------------------------
# DELETE /providers/{provider_id}
# ---------------------------------------------------------------------------


class TestDeleteProvider:
    """Tests for DELETE /providers/{provider_id} endpoint."""

    def test_returns_200_on_success(self, api_client):
        """DELETE /providers/math returns HTTP 200."""
        response = api_client.delete("/providers/math")
        assert response.status_code == 200

    def test_returns_deleted_flag(self, api_client):
        """DELETE /providers/math returns deleted=True in body."""
        response = api_client.delete("/providers/math")
        data = response.json()
        assert data["provider_id"] == "math"
        assert data["deleted"] is True

    def test_returns_404_for_unknown_provider(self, api_client):
        """DELETE /providers/unknown returns HTTP 404."""
        response = api_client.delete("/providers/unknown")
        assert response.status_code == 404

    def test_dispatches_delete_provider_command(self, api_client, mock_context):
        """DELETE /providers/math dispatches DeleteProviderCommand."""
        api_client.delete("/providers/math")
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], DeleteProviderCommand) for call in calls)

    def test_command_includes_provider_id_from_path(self, api_client, mock_context):
        """DELETE /providers/math dispatches command with correct provider_id."""
        api_client.delete("/providers/math")
        calls = mock_context.command_bus.send.call_args_list
        cmd = next(c[0][0] for c in calls if isinstance(c[0][0], DeleteProviderCommand))
        assert cmd.provider_id == "math"
