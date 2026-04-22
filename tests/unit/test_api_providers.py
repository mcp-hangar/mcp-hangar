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

from mcp_hangar.application.commands.commands import StartMcpServerCommand, StopMcpServerCommand
from mcp_hangar.application.queries.queries import (
    GetMcpServerHealthQuery,
    GetMcpServerQuery,
    GetMcpServerToolsQuery,
    ListMcpServersQuery,
)
from mcp_hangar.application.read_models.mcp_server_views import (
    HealthInfo,
    McpServerDetails,
    McpServerSummary,
    ToolInfo,
)
from mcp_hangar.domain.exceptions import McpServerNotFoundError


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
    return McpServerSummary(
        mcp_server_id="math",
        state="ready",
        mode="subprocess",
        is_alive=True,
        tools_count=1,
        health_status="healthy",
    )


@pytest.fixture
def provider_details(tool_info, health_info):
    """Sample ProviderDetails read model."""
    return McpServerDetails(
        mcp_server_id="math",
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
        if isinstance(query, ListMcpServersQuery):
            if query.state_filter:
                return [s for s in [provider_summary] if s.state == query.state_filter]
            return [provider_summary]
        elif isinstance(query, GetMcpServerQuery):
            if query.mcp_server_id == "math":
                return provider_details
            raise McpServerNotFoundError(query.mcp_server_id)
        elif isinstance(query, GetMcpServerToolsQuery):
            if query.mcp_server_id == "math":
                return [tool_info]
            raise McpServerNotFoundError(query.mcp_server_id)
        elif isinstance(query, GetMcpServerHealthQuery):
            if query.mcp_server_id == "math":
                return health_info
            raise McpServerNotFoundError(query.mcp_server_id)
        raise ValueError(f"Unexpected query: {type(query)}")

    def send_command(command):
        if isinstance(command, StartMcpServerCommand):
            if command.mcp_server_id == "math":
                return {"status": "started", "mcp_server": "math"}
            raise McpServerNotFoundError(command.mcp_server_id)
        elif isinstance(command, StopMcpServerCommand):
            if command.mcp_server_id == "math":
                return {"status": "stopped", "mcp_server": "math"}
            raise McpServerNotFoundError(command.mcp_server_id)
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
# GET /mcp_servers
# ---------------------------------------------------------------------------


class TestListProviders:
    """Tests for GET /mcp_servers."""

    def test_returns_200(self, api_client):
        """GET /mcp_servers returns HTTP 200."""
        response = api_client.get("/mcp_servers/")
        assert response.status_code == 200

    def test_returns_provider_list(self, api_client):
        """GET /mcp_servers returns list with mcp_servers key."""
        response = api_client.get("/mcp_servers/")
        data = response.json()
        assert "mcp_servers" in data
        assert isinstance(data["mcp_servers"], list)

    def test_returns_provider_with_expected_fields(self, api_client):
        """GET /mcp_servers returns mcp_servers with expected fields."""
        response = api_client.get("/mcp_servers/")
        data = response.json()
        provider = data["mcp_servers"][0]
        assert provider["mcp_server_id"] == "math"
        assert provider["state"] == "ready"
        assert provider["tools_count"] == 1

    def test_state_filter_ready_returns_matching(self, api_client):
        """GET /mcp_servers?state=ready returns only ready mcp_servers."""
        response = api_client.get("/mcp_servers/?state=ready")
        data = response.json()
        assert len(data["mcp_servers"]) == 1
        assert data["mcp_servers"][0]["state"] == "ready"

    def test_state_filter_cold_returns_empty(self, api_client):
        """GET /mcp_servers?state=cold returns empty list when no cold mcp_servers."""
        response = api_client.get("/mcp_servers/?state=cold")
        data = response.json()
        assert data["mcp_servers"] == []


# ---------------------------------------------------------------------------
# GET /mcp_servers/{id}
# ---------------------------------------------------------------------------


class TestGetProvider:
    """Tests for GET /mcp_servers/{id}."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /mcp_servers/math returns HTTP 200."""
        response = api_client.get("/mcp_servers/math")
        assert response.status_code == 200

    def test_returns_provider_details(self, api_client):
        """GET /mcp_servers/math returns mcp_server details."""
        response = api_client.get("/mcp_servers/math")
        data = response.json()
        assert data["mcp_server_id"] == "math"
        assert data["state"] == "ready"
        assert "tools" in data
        assert "health" in data

    def test_returns_tools_in_details(self, api_client):
        """GET /mcp_servers/math includes tools array."""
        response = api_client.get("/mcp_servers/math")
        data = response.json()
        assert len(data["tools"]) == 1
        assert data["tools"][0]["name"] == "add"

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /mcp_servers/unknown returns HTTP 404."""
        response = api_client.get("/mcp_servers/unknown")
        assert response.status_code == 404

    def test_returns_error_envelope_for_404(self, api_client):
        """GET /mcp_servers/unknown returns error envelope format."""
        response = api_client.get("/mcp_servers/unknown")
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "McpServerNotFoundError"
        assert "message" in data["error"]
        assert "details" in data["error"]


# ---------------------------------------------------------------------------
# POST /mcp_servers/{id}/start
# ---------------------------------------------------------------------------


class TestStartProvider:
    """Tests for POST /mcp_servers/{id}/start."""

    def test_returns_200_for_known_provider(self, api_client):
        """POST /mcp_servers/math/start returns HTTP 200."""
        response = api_client.post("/mcp_servers/math/start")
        assert response.status_code == 200

    def test_returns_start_result(self, api_client):
        """POST /mcp_servers/math/start returns start result."""
        response = api_client.post("/mcp_servers/math/start")
        data = response.json()
        assert data["status"] == "started"
        assert data["mcp_server"] == "math"

    def test_returns_404_for_unknown_provider(self, api_client):
        """POST /mcp_servers/unknown/start returns HTTP 404."""
        response = api_client.post("/mcp_servers/unknown/start")
        assert response.status_code == 404

    def test_dispatches_start_provider_command(self, api_client, mock_context):
        """POST /mcp_servers/math/start dispatches StartMcpServerCommand."""
        response = api_client.post("/mcp_servers/math/start")
        assert response.status_code == 200
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], StartMcpServerCommand) for call in calls)


# ---------------------------------------------------------------------------
# POST /mcp_servers/{id}/stop
# ---------------------------------------------------------------------------


class TestStopProvider:
    """Tests for POST /mcp_servers/{id}/stop."""

    def test_returns_200_for_known_provider(self, api_client):
        """POST /mcp_servers/math/stop returns HTTP 200."""
        response = api_client.post("/mcp_servers/math/stop")
        assert response.status_code == 200

    def test_returns_stop_result(self, api_client):
        """POST /mcp_servers/math/stop returns stop result."""
        response = api_client.post("/mcp_servers/math/stop")
        data = response.json()
        assert data["status"] == "stopped"

    def test_dispatches_stop_provider_command(self, api_client, mock_context):
        """POST /mcp_servers/math/stop dispatches StopMcpServerCommand."""
        response = api_client.post("/mcp_servers/math/stop")
        assert response.status_code == 200
        calls = mock_context.command_bus.send.call_args_list
        assert any(isinstance(call[0][0], StopMcpServerCommand) for call in calls)


# ---------------------------------------------------------------------------
# GET /mcp_servers/{id}/tools
# ---------------------------------------------------------------------------


class TestGetProviderTools:
    """Tests for GET /mcp_servers/{id}/tools."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /mcp_servers/math/tools returns HTTP 200."""
        response = api_client.get("/mcp_servers/math/tools")
        assert response.status_code == 200

    def test_returns_tools_list(self, api_client):
        """GET /mcp_servers/math/tools returns tools array."""
        response = api_client.get("/mcp_servers/math/tools")
        data = response.json()
        assert "tools" in data
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 1

    def test_returns_tool_with_json_schema(self, api_client):
        """GET /mcp_servers/math/tools returns tool with inputSchema."""
        response = api_client.get("/mcp_servers/math/tools")
        data = response.json()
        tool = data["tools"][0]
        assert tool["name"] == "add"
        assert "inputSchema" in tool

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /mcp_servers/unknown/tools returns HTTP 404."""
        response = api_client.get("/mcp_servers/unknown/tools")
        assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /mcp_servers/{id}/health
# ---------------------------------------------------------------------------


class TestGetProviderHealth:
    """Tests for GET /mcp_servers/{id}/health."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /mcp_servers/math/health returns HTTP 200."""
        response = api_client.get("/mcp_servers/math/health")
        assert response.status_code == 200

    def test_returns_health_info(self, api_client):
        """GET /mcp_servers/math/health returns health info."""
        response = api_client.get("/mcp_servers/math/health")
        data = response.json()
        assert "consecutive_failures" in data
        assert "success_rate" in data
        assert "can_retry" in data

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /mcp_servers/unknown/health returns HTTP 404."""
        response = api_client.get("/mcp_servers/unknown/health")
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
            list=Mock(return_value={"mcp_servers": []}),
            start=Mock(return_value={"status": "started"}),
            stop=Mock(return_value={"status": "stopped"}),
            invoke=Mock(return_value={"result": 42}),
            tools=Mock(return_value={"tools": []}),
            details=Mock(return_value={"mcp_server": "test"}),
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
        """GET /api/mcp_servers is reachable via the /api prefix."""
        response = full_app_client.get("/api/mcp_servers/")
        assert response.status_code == 200

    def test_ready_endpoint_still_works(self, full_app_client):
        """GET /ready still works after adding API mount."""
        response = full_app_client.get("/ready")
        assert response.status_code in (200, 503)  # 503 when checks fail in test env

    def test_metrics_endpoint_still_works(self, full_app_client):
        """GET /metrics still works after adding API mount."""
        response = full_app_client.get("/metrics")
        assert response.status_code == 200
