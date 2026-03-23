"""Tests for provider REST API endpoints and ASGI mount integration.

Tests cover:
- All provider CRUD endpoints (list, get, start, stop, tools, health)
- State filtering
- Error responses (404 for unknown providers)
- ASGI routing: /api/* routed to API app, /health still works
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.commands.commands import StartProviderCommand, StopProviderCommand
from mcp_hangar.application.queries.queries import (
    GetProviderHealthQuery,
    GetProviderQuery,
    GetProviderToolsQuery,
    ListProvidersQuery,
)
from mcp_hangar.application.read_models.provider_views import (
    HealthInfo,
    ProviderDetails,
    ProviderSummary,
    ToolInfo,
)
from mcp_hangar.domain.exceptions import ProviderNotFoundError


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def health_info():
    """Sample HealthInfo read model."""
    return HealthInfo(
        consecutive_failures=0,
        total_invocations=10,
        total_failures=1,
        success_rate=0.9,
        can_retry=True,
        last_success_ago=5.0,
        last_failure_ago=100.0,
    )


@pytest.fixture
def tool_info():
    """Sample ToolInfo read model."""
    return ToolInfo(
        name="add",
        description="Add two numbers",
        input_schema={"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}},
    )


@pytest.fixture
def provider_summary():
    """Sample ProviderSummary read model."""
    return ProviderSummary(
        provider_id="math",
        state="ready",
        mode="subprocess",
        is_alive=True,
        tools_count=1,
        health_status="healthy",
    )


@pytest.fixture
def provider_details(tool_info, health_info):
    """Sample ProviderDetails read model."""
    return ProviderDetails(
        provider_id="math",
        state="ready",
        mode="subprocess",
        is_alive=True,
        tools=[tool_info],
        health=health_info,
        idle_time=0.0,
        meta={},
    )


@pytest.fixture
def mock_context(provider_summary, provider_details, tool_info, health_info):
    """Mock ApplicationContext with query bus and command bus."""
    ctx = Mock()
    command_bus = Mock()
    query_bus = Mock()

    def execute_query(query):
        if isinstance(query, ListProvidersQuery):
            if query.state_filter:
                return [s for s in [provider_summary] if s.state == query.state_filter]
            return [provider_summary]
        elif isinstance(query, GetProviderQuery):
            if query.provider_id == "math":
                return provider_details
            raise ProviderNotFoundError(query.provider_id)
        elif isinstance(query, GetProviderToolsQuery):
            if query.provider_id == "math":
                return [tool_info]
            raise ProviderNotFoundError(query.provider_id)
        elif isinstance(query, GetProviderHealthQuery):
            if query.provider_id == "math":
                return health_info
            raise ProviderNotFoundError(query.provider_id)
        raise ValueError(f"Unexpected query: {type(query)}")

    def send_command(command):
        if isinstance(command, StartProviderCommand):
            if command.provider_id == "math":
                return {"status": "started", "provider": "math"}
            raise ProviderNotFoundError(command.provider_id)
        elif isinstance(command, StopProviderCommand):
            if command.provider_id == "math":
                return {"status": "stopped", "provider": "math"}
            raise ProviderNotFoundError(command.provider_id)
        raise ValueError(f"Unexpected command: {type(command)}")

    query_bus.execute.side_effect = execute_query
    command_bus.send.side_effect = send_command
    ctx.query_bus = query_bus
    ctx.command_bus = command_bus
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
# GET /providers
# ---------------------------------------------------------------------------


class TestListProviders:
    """Tests for GET /providers."""

    def test_returns_200(self, api_client):
        """GET /providers returns HTTP 200."""
        response = api_client.get("/providers/")
        assert response.status_code == 200

    def test_returns_provider_list(self, api_client):
        """GET /providers returns list with providers key."""
        response = api_client.get("/providers/")
        data = response.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)

    def test_returns_provider_with_expected_fields(self, api_client):
        """GET /providers returns providers with expected fields."""
        response = api_client.get("/providers/")
        data = response.json()
        provider = data["providers"][0]
        assert provider["provider_id"] == "math"
        assert provider["state"] == "ready"
        assert provider["tools_count"] == 1

    def test_state_filter_ready_returns_matching(self, api_client):
        """GET /providers?state=ready returns only ready providers."""
        response = api_client.get("/providers/?state=ready")
        data = response.json()
        assert len(data["providers"]) == 1
        assert data["providers"][0]["state"] == "ready"

    def test_state_filter_cold_returns_empty(self, api_client):
        """GET /providers?state=cold returns empty list when no cold providers."""
        response = api_client.get("/providers/?state=cold")
        data = response.json()
        assert data["providers"] == []


# ---------------------------------------------------------------------------
# GET /providers/{id}
# ---------------------------------------------------------------------------


class TestGetProvider:
    """Tests for GET /providers/{id}."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /providers/math returns HTTP 200."""
        response = api_client.get("/providers/math")
        assert response.status_code == 200

    def test_returns_provider_details(self, api_client):
        """GET /providers/math returns provider details."""
        response = api_client.get("/providers/math")
        data = response.json()
        assert data["provider_id"] == "math"
        assert data["state"] == "ready"
        assert "tools" in data
        assert "health" in data

    def test_returns_tools_in_details(self, api_client):
        """GET /providers/math includes tools array."""
        response = api_client.get("/providers/math")
        data = response.json()
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "add"

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /providers/unknown returns HTTP 404."""
        response = api_client.get("/providers/unknown")
        assert response.status_code == 404

    def test_returns_error_envelope_for_404(self, api_client):
        """GET /providers/unknown returns error envelope format."""
        response = api_client.get("/providers/unknown")
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "ProviderNotFoundError"
        assert "message" in data["error"]
        assert "details" in data["error"]


# ---------------------------------------------------------------------------
# POST /providers/{id}/start
# ---------------------------------------------------------------------------


class TestStartProvider:
    """Tests for POST /providers/{id}/start."""

    def test_returns_200_for_known_provider(self, api_client):
        """POST /providers/math/start returns HTTP 200."""
        response = api_client.post("/providers/math/start")
        assert response.status_code == 200

    def test_returns_start_result(self, api_client):
        """POST /providers/math/start returns start result."""
        response = api_client.post("/providers/math/start")
        data = response.json()
        assert data["status"] == "started"
        assert data["provider"] == "math"

    def test_returns_404_for_unknown_provider(self, api_client):
        """POST /providers/unknown/start returns HTTP 404."""
        response = api_client.post("/providers/unknown/start")
        assert response.status_code == 404

    def test_dispatches_start_provider_command(self, api_client, mock_context):
        """POST /providers/math/start dispatches StartProviderCommand."""
        response = api_client.post("/providers/math/start")
        assert response.status_code == 200
        # Verify command bus was called
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], StartProviderCommand) for call in calls)


# ---------------------------------------------------------------------------
# POST /providers/{id}/stop
# ---------------------------------------------------------------------------


class TestStopProvider:
    """Tests for POST /providers/{id}/stop."""

    def test_returns_200_for_known_provider(self, api_client):
        """POST /providers/math/stop returns HTTP 200."""
        response = api_client.post("/providers/math/stop")
        assert response.status_code == 200

    def test_returns_stop_result(self, api_client):
        """POST /providers/math/stop returns stop result."""
        response = api_client.post("/providers/math/stop")
        data = response.json()
        assert data["status"] == "stopped"

    def test_dispatches_stop_provider_command(self, api_client, mock_context):
        """POST /providers/math/stop dispatches StopProviderCommand."""
        response = api_client.post("/providers/math/stop")
        assert response.status_code == 200
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], StopProviderCommand) for call in calls)


# ---------------------------------------------------------------------------
# GET /providers/{id}/tools
# ---------------------------------------------------------------------------


class TestGetProviderTools:
    """Tests for GET /providers/{id}/tools."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /providers/math/tools returns HTTP 200."""
        response = api_client.get("/providers/math/tools")
        assert response.status_code == 200

    def test_returns_tools_list(self, api_client):
        """GET /providers/math/tools returns tools array."""
        response = api_client.get("/providers/math/tools")
        data = response.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 1

    def test_returns_tool_with_json_schema(self, api_client):
        """GET /providers/math/tools returns tool with inputSchema."""
        response = api_client.get("/providers/math/tools")
        data = response.json()
        tool = data["tools"][0]
        assert tool["name"] == "add"
        assert "inputSchema" in tool

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /providers/unknown/tools returns HTTP 404."""
        response = api_client.get("/providers/unknown/tools")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /providers/{id}/health
# ---------------------------------------------------------------------------


class TestGetProviderHealth:
    """Tests for GET /providers/{id}/health."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /providers/math/health returns HTTP 200."""
        response = api_client.get("/providers/math/health")
        assert response.status_code == 200

    def test_returns_health_info(self, api_client):
        """GET /providers/math/health returns health info."""
        response = api_client.get("/providers/math/health")
        data = response.json()
        assert "consecutive_failures" in data
        assert "success_rate" in data
        assert "can_retry" in data

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /providers/unknown/health returns HTTP 404."""
        response = api_client.get("/providers/unknown/health")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# ASGI integration: API mount + existing routes
# ---------------------------------------------------------------------------


class TestASGIIntegration:
    """Tests verifying the API is mounted at /api/ and existing routes still work."""

    @pytest.fixture
    def full_app_client(self, mock_context):
        """TestClient for the full combined ASGI app."""
        from unittest.mock import Mock

        from mcp_hangar.fastmcp_server.factory import MCPServerFactory
        from mcp_hangar.fastmcp_server.config import HangarFunctions

        hangar = HangarFunctions(
            list=Mock(return_value={"providers": []}),
            start=Mock(return_value={"status": "started"}),
            stop=Mock(return_value={"status": "stopped"}),
            invoke=Mock(return_value={"result": 42}),
            tools=Mock(return_value={"tools": []}),
            details=Mock(return_value={"provider": "test"}),
            health=Mock(return_value={"status": "healthy"}),
        )

        factory = MCPServerFactory(hangar)

        with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
            with patch("mcp_hangar.fastmcp_server.factory.MCPServerFactory.create_server") as mock_server:
                # Avoid actually starting server in test
                from starlette.responses import JSONResponse

                async def mcp_endpoint(scope, receive, send):
                    response = JSONResponse({"mcp": "ok"})
                    await response(scope, receive, send)

                mock_server.return_value = Mock()
                mock_server.return_value.streamable_http_app.return_value = mcp_endpoint

                app = factory.create_asgi_app()
                yield TestClient(app, raise_server_exceptions=False)

    def test_health_endpoint_still_works(self, full_app_client):
        """GET /health still returns ok response."""
        response = full_app_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_api_providers_reachable_under_api_prefix(self, full_app_client):
        """GET /api/providers is reachable via the /api prefix."""
        response = full_app_client.get("/api/providers/")
        assert response.status_code == 200

    def test_ready_endpoint_still_works(self, full_app_client):
        """GET /ready still works after adding API mount."""
        response = full_app_client.get("/ready")
        assert response.status_code in (200, 503)  # 503 when checks fail in test env

    def test_metrics_endpoint_still_works(self, full_app_client):
        """GET /metrics still works after adding API mount."""
        response = full_app_client.get("/metrics")
        assert response.status_code == 200
