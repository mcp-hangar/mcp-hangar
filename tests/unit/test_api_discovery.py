"""Tests for discovery REST API endpoints.

Tests cover:
- GET /discovery/sources returns list of source statuses
- GET /discovery/sources returns 404 when discovery not configured (None)
- GET /discovery/pending returns list of pending providers
- GET /discovery/quarantined returns quarantined providers dict
- POST /discovery/approve/{name} approves a provider
- POST /discovery/reject/{name} rejects a provider
- All endpoints return 404 when discovery_orchestrator is None
"""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_discovered_provider(name: str = "my-provider") -> Mock:
    """Create a mock DiscoveredProvider."""
    provider = Mock()
    provider.name = name
    provider.source_type = "docker"
    provider.command = ["python", "-m", "my_provider"]
    provider.url = None
    provider.mode = "subprocess"
    provider.discovered_at = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    provider.metadata = {"version": "1.0"}
    provider.to_dict.return_value = {
        "name": name,
        "source_type": "docker",
        "command": ["python", "-m", "my_provider"],
        "url": None,
        "mode": "subprocess",
        "discovered_at": "2026-01-01T12:00:00+00:00",
        "metadata": {"version": "1.0"},
    }
    return provider


@pytest.fixture
def mock_orchestrator():
    """Mock DiscoveryOrchestrator with all methods."""
    orchestrator = Mock()
    # Sync methods
    orchestrator.get_pending_mcp_servers.return_value = [_make_discovered_provider("pending-1")]
    orchestrator.get_quarantined.return_value = {
        "bad-provider": {
            "provider": {"name": "bad-provider"},
            "reason": "failed security validation",
            "quarantine_time": "2026-01-01T10:00:00+00:00",
        }
    }
    # Async methods
    orchestrator.get_sources_status = AsyncMock(
        return_value=[
            {
                "source_type": "docker",
                "mode": "additive",
                "is_healthy": True,
                "providers_count": 5,
            }
        ]
    )
    orchestrator.approve_mcp_server = AsyncMock(
        return_value={"approved": True, "mcp_server": "pending-1", "status": "registered"}
    )
    orchestrator.reject_mcp_server = AsyncMock(return_value={"rejected": True, "mcp_server": "bad-provider"})
    return orchestrator


@pytest.fixture
def mock_context(mock_orchestrator):
    """Mock ApplicationContext with discovery_orchestrator."""
    ctx = Mock()
    ctx.discovery_orchestrator = mock_orchestrator
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()
    return ctx


@pytest.fixture
def mock_context_no_discovery():
    """Mock ApplicationContext without discovery configured."""
    ctx = Mock()
    ctx.discovery_orchestrator = None
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient for the API app with mocked context."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.discovery.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


@pytest.fixture
def no_discovery_client(mock_context_no_discovery):
    """Starlette TestClient for the API app without discovery."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context_no_discovery):
        with patch("mcp_hangar.server.api.discovery.get_context", return_value=mock_context_no_discovery):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# GET /discovery/sources
# ---------------------------------------------------------------------------


class TestListSources:
    """Tests for GET /discovery/sources."""

    def test_returns_200(self, api_client):
        """GET /discovery/sources returns HTTP 200."""
        response = api_client.get("/discovery/sources")
        assert response.status_code == 200

    def test_returns_sources_key(self, api_client):
        """GET /discovery/sources returns JSON with 'sources' key."""
        response = api_client.get("/discovery/sources")
        data = response.json()
        assert "sources" in data
        assert isinstance(data["sources"], list)

    def test_returns_source_data(self, api_client):
        """GET /discovery/sources returns source status info."""
        response = api_client.get("/discovery/sources")
        data = response.json()
        assert len(data["sources"]) == 1
        source = data["sources"][0]
        assert source["source_type"] == "docker"
        assert source["is_healthy"] is True

    def test_returns_404_when_discovery_not_configured(self, no_discovery_client):
        """GET /discovery/sources returns 404 when discovery_orchestrator is None."""
        response = no_discovery_client.get("/discovery/sources")
        assert response.status_code == 404

    def test_returns_error_envelope_when_not_configured(self, no_discovery_client):
        """GET /discovery/sources returns error envelope when not configured."""
        response = no_discovery_client.get("/discovery/sources")
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "DiscoveryNotConfigured"


# ---------------------------------------------------------------------------
# GET /discovery/pending
# ---------------------------------------------------------------------------


class TestListPending:
    """Tests for GET /discovery/pending."""

    def test_returns_200(self, api_client):
        """GET /discovery/pending returns HTTP 200."""
        response = api_client.get("/discovery/pending")
        assert response.status_code == 200

    def test_returns_pending_key(self, api_client):
        """GET /discovery/pending returns JSON with 'pending' key."""
        response = api_client.get("/discovery/pending")
        data = response.json()
        assert "pending" in data
        assert isinstance(data["pending"], list)

    def test_returns_pending_provider_data(self, api_client):
        """GET /discovery/pending returns provider data."""
        response = api_client.get("/discovery/pending")
        data = response.json()
        assert len(data["pending"]) == 1
        provider = data["pending"][0]
        assert provider["name"] == "pending-1"

    def test_returns_404_when_discovery_not_configured(self, no_discovery_client):
        """GET /discovery/pending returns 404 when discovery_orchestrator is None."""
        response = no_discovery_client.get("/discovery/pending")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /discovery/quarantined
# ---------------------------------------------------------------------------


class TestListQuarantined:
    """Tests for GET /discovery/quarantined."""

    def test_returns_200(self, api_client):
        """GET /discovery/quarantined returns HTTP 200."""
        response = api_client.get("/discovery/quarantined")
        assert response.status_code == 200

    def test_returns_quarantined_key(self, api_client):
        """GET /discovery/quarantined returns JSON with 'quarantined' key."""
        response = api_client.get("/discovery/quarantined")
        data = response.json()
        assert "quarantined" in data
        assert isinstance(data["quarantined"], dict)

    def test_returns_quarantined_providers(self, api_client):
        """GET /discovery/quarantined returns quarantined provider info."""
        response = api_client.get("/discovery/quarantined")
        data = response.json()
        assert "bad-provider" in data["quarantined"]
        entry = data["quarantined"]["bad-provider"]
        assert "reason" in entry
        assert entry["reason"] == "failed security validation"

    def test_returns_404_when_discovery_not_configured(self, no_discovery_client):
        """GET /discovery/quarantined returns 404 when discovery_orchestrator is None."""
        response = no_discovery_client.get("/discovery/quarantined")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /discovery/approve/{name}
# ---------------------------------------------------------------------------


class TestApproveProvider:
    """Tests for POST /discovery/approve/{name}."""

    def test_returns_200(self, api_client):
        """POST /discovery/approve/pending-1 returns HTTP 200."""
        response = api_client.post("/discovery/approve/pending-1")
        assert response.status_code == 200

    def test_returns_approval_result(self, api_client):
        """POST /discovery/approve/pending-1 returns approval result."""
        response = api_client.post("/discovery/approve/pending-1")
        data = response.json()
        assert data["approved"] is True
        assert data["mcp_server"] == "pending-1"

    def test_calls_approve_provider_on_orchestrator(self, api_client, mock_context):
        """POST /discovery/approve/pending-1 calls approve_provider on orchestrator."""
        response = api_client.post("/discovery/approve/pending-1")
        assert response.status_code == 200
        mock_context.discovery_orchestrator.approve_mcp_server.assert_called_once_with("pending-1")

    def test_returns_404_when_discovery_not_configured(self, no_discovery_client):
        """POST /discovery/approve/name returns 404 when discovery not configured."""
        response = no_discovery_client.post("/discovery/approve/some-provider")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# POST /discovery/reject/{name}
# ---------------------------------------------------------------------------


class TestRejectProvider:
    """Tests for POST /discovery/reject/{name}."""

    def test_returns_200(self, api_client):
        """POST /discovery/reject/bad-provider returns HTTP 200."""
        response = api_client.post("/discovery/reject/bad-provider")
        assert response.status_code == 200

    def test_returns_rejection_result(self, api_client):
        """POST /discovery/reject/bad-provider returns rejection result."""
        response = api_client.post("/discovery/reject/bad-provider")
        data = response.json()
        assert data["rejected"] is True
        assert data["mcp_server"] == "bad-provider"

    def test_calls_reject_provider_on_orchestrator(self, api_client, mock_context):
        """POST /discovery/reject/bad-provider calls reject_provider on orchestrator."""
        response = api_client.post("/discovery/reject/bad-provider")
        assert response.status_code == 200
        mock_context.discovery_orchestrator.reject_mcp_server.assert_called_once_with("bad-provider")

    def test_returns_404_when_discovery_not_configured(self, no_discovery_client):
        """POST /discovery/reject/name returns 404 when discovery not configured."""
        response = no_discovery_client.post("/discovery/reject/some-provider")
        assert response.status_code == 404
