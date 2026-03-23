"""Unit tests for discovery source management REST API endpoints (DISC-02).

Tests cover the 5 new endpoints added to /discovery/sources in Plan 04:
POST /sources, PUT /sources/{id}, DELETE /sources/{id},
POST /sources/{id}/scan, PUT /sources/{id}/enable.
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
    TriggerSourceScanCommand,
    UpdateDiscoverySourceCommand,
)
from mcp_hangar.domain.exceptions import ProviderNotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_context():
    """Mock ApplicationContext with command bus + discovery orchestrator."""
    ctx = Mock()
    command_bus = Mock()
    query_bus = Mock()

    def send_command(command):
        if isinstance(command, RegisterDiscoverySourceCommand):
            return {"source_id": "new-uuid", "registered": True}
        if isinstance(command, UpdateDiscoverySourceCommand):
            if command.source_id == "known-id":
                return {"source_id": "known-id", "updated": True}
            raise ProviderNotFoundError(provider_id=command.source_id)
        if isinstance(command, DeregisterDiscoverySourceCommand):
            if command.source_id == "known-id":
                return {"source_id": "known-id", "deregistered": True}
            raise ProviderNotFoundError(provider_id=command.source_id)
        if isinstance(command, TriggerSourceScanCommand):
            if command.source_id == "known-id":
                return {"source_id": "known-id", "scan_triggered": True, "providers_found": 2}
            raise ProviderNotFoundError(provider_id=command.source_id)
        if isinstance(command, ToggleDiscoverySourceCommand):
            if command.source_id == "known-id":
                return {"source_id": "known-id", "enabled": command.enabled}
            raise ProviderNotFoundError(provider_id=command.source_id)
        raise ValueError(f"Unexpected command: {type(command)}")

    command_bus.send.side_effect = send_command
    ctx.command_bus = command_bus
    ctx.query_bus = query_bus
    ctx.discovery_orchestrator = Mock()  # required by _require_orchestrator()
    ctx.catalog_repository = None
    ctx.groups = {}
    return ctx


@pytest.fixture
def api_client(mock_context):
    """TestClient with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.discovery.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# POST /discovery/sources
# ---------------------------------------------------------------------------


class TestRegisterDiscoverySourceEndpoint:
    """Tests for POST /discovery/sources."""

    def test_returns_201_on_success(self, api_client):
        """POST /discovery/sources returns HTTP 201."""
        response = api_client.post(
            "/discovery/sources",
            json={"source_type": "docker", "mode": "additive"},
        )
        assert response.status_code == 201

    def test_returns_source_id_and_registered_true(self, api_client):
        """POST /discovery/sources returns source_id and registered=True."""
        response = api_client.post(
            "/discovery/sources",
            json={"source_type": "docker", "mode": "additive"},
        )
        data = response.json()
        assert data["source_id"] == "new-uuid"
        assert data["registered"] is True

    def test_dispatches_register_discovery_source_command(self, api_client, mock_context):
        """POST /discovery/sources dispatches RegisterDiscoverySourceCommand."""
        api_client.post("/discovery/sources", json={"source_type": "filesystem", "mode": "additive"})
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(c[0][0], RegisterDiscoverySourceCommand) for c in calls)


# ---------------------------------------------------------------------------
# PUT /discovery/sources/{source_id}
# ---------------------------------------------------------------------------


class TestUpdateDiscoverySourceEndpoint:
    """Tests for PUT /discovery/sources/{source_id}."""

    def test_returns_200_on_success(self, api_client):
        """PUT /discovery/sources/known-id returns HTTP 200."""
        response = api_client.put("/discovery/sources/known-id", json={"enabled": False})
        assert response.status_code == 200

    def test_returns_updated_true(self, api_client):
        """PUT /discovery/sources/known-id returns updated=True."""
        response = api_client.put("/discovery/sources/known-id", json={"enabled": False})
        assert response.json()["updated"] is True

    def test_returns_404_for_unknown_source(self, api_client):
        """PUT /discovery/sources/unknown returns HTTP 404."""
        response = api_client.put("/discovery/sources/unknown", json={"enabled": False})
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /discovery/sources/{source_id}
# ---------------------------------------------------------------------------


class TestDeregisterDiscoverySourceEndpoint:
    """Tests for DELETE /discovery/sources/{source_id}."""

    def test_returns_200_on_success(self, api_client):
        """DELETE /discovery/sources/known-id returns HTTP 200."""
        response = api_client.delete("/discovery/sources/known-id")
        assert response.status_code == 200

    def test_returns_deregistered_true(self, api_client):
        """DELETE /discovery/sources/known-id returns deregistered=True."""
        assert api_client.delete("/discovery/sources/known-id").json()["deregistered"] is True

    def test_returns_404_for_unknown_source(self, api_client):
        """DELETE /discovery/sources/unknown returns HTTP 404."""
        assert api_client.delete("/discovery/sources/unknown").status_code == 404


# ---------------------------------------------------------------------------
# POST /discovery/sources/{source_id}/scan
# ---------------------------------------------------------------------------


class TestTriggerScanEndpoint:
    """Tests for POST /discovery/sources/{source_id}/scan."""

    def test_returns_200_with_providers_found(self, api_client):
        """POST /discovery/sources/known-id/scan returns HTTP 200 with providers_found."""
        response = api_client.post("/discovery/sources/known-id/scan")
        assert response.status_code == 200
        assert response.json()["providers_found"] == 2

    def test_returns_404_for_unknown_source(self, api_client):
        """POST /discovery/sources/unknown/scan returns HTTP 404."""
        assert api_client.post("/discovery/sources/unknown/scan").status_code == 404


# ---------------------------------------------------------------------------
# PUT /discovery/sources/{source_id}/enable
# ---------------------------------------------------------------------------


class TestToggleSourceEndpoint:
    """Tests for PUT /discovery/sources/{source_id}/enable."""

    def test_enable_returns_enabled_true(self, api_client):
        """PUT /discovery/sources/known-id/enable with enabled=True returns enabled=True."""
        response = api_client.put("/discovery/sources/known-id/enable", json={"enabled": True})
        assert response.status_code == 200
        assert response.json()["enabled"] is True

    def test_disable_returns_enabled_false(self, api_client):
        """PUT /discovery/sources/known-id/enable with enabled=False returns enabled=False."""
        response = api_client.put("/discovery/sources/known-id/enable", json={"enabled": False})
        assert response.json()["enabled"] is False

    def test_returns_404_for_unknown_source(self, api_client):
        """PUT /discovery/sources/unknown/enable returns HTTP 404."""
        assert api_client.put("/discovery/sources/unknown/enable", json={"enabled": True}).status_code == 404
