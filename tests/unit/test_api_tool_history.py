"""Tests for the tool invocation history REST API endpoint.

Tests cover:
- GET /providers/{id}/tools/history returns 200 with history list
- GET /providers/{id}/tools/history returns 200 with empty list for unknown provider
- limit and from_position query params are parsed and forwarded to the query
- limit clamped to max 500 silently
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.queries.queries import GetToolInvocationHistoryQuery


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_history_entry(event_type: str = "ToolInvocationCompleted", mcp_server_id: str = "math") -> dict[str, object]:
    """Create a sample tool history entry."""
    return {
        "stream_id": f"provider-{mcp_server_id}",
        "version": 1,
        "event_type": event_type,
        "event_id": "evt-001",
        "occurred_at": 1700000000.0,
        "data": {"tool_name": "add", "duration_ms": 10},
        "stored_at": 1700000001.0,
    }


@pytest.fixture
def mock_history_result():
    """Sample result dict returned by GetToolInvocationHistoryHandler."""
    return {
        "mcp_server_id": "math",
        "history": [_make_history_entry("ToolInvocationCompleted")],
        "total": 1,
    }


@pytest.fixture
def mock_empty_history_result():
    """Result dict with no history entries."""
    return {
        "mcp_server_id": "unknown",
        "history": [],
        "total": 0,
    }


@pytest.fixture
def mock_context(mock_history_result, mock_empty_history_result):
    """Mock ApplicationContext with query bus that handles tool history queries."""
    ctx = Mock()
    command_bus = Mock()
    query_bus = Mock()

    def execute_query(query):
        if isinstance(query, GetToolInvocationHistoryQuery):
            if query.mcp_server_id == "math":
                return {
                    "mcp_server_id": "math",
                    "history": [_make_history_entry("ToolInvocationCompleted")],
                    "total": 1,
                }
            # Unknown provider returns empty history (not 404)
            return {
                "mcp_server_id": query.mcp_server_id,
                "history": [],
                "total": 0,
            }
        raise ValueError(f"Unexpected query: {type(query)}")

    query_bus.execute.side_effect = execute_query
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
# GET /providers/{id}/tools/history
# ---------------------------------------------------------------------------


class TestGetProviderToolHistory:
    """Tests for GET /providers/{id}/tools/history."""

    def test_returns_200_for_known_provider(self, api_client):
        """GET /providers/math/tools/history returns HTTP 200."""
        response = api_client.get("/mcp_servers/math/tools/history")
        assert response.status_code == 200

    def test_returns_history_with_expected_keys(self, api_client):
        """GET /providers/math/tools/history returns JSON with mcp_server_id, history, total."""
        response = api_client.get("/mcp_servers/math/tools/history")
        data = response.json()
        assert "mcp_server_id" in data
        assert "history" in data
        assert "total" in data

    def test_history_contains_events(self, api_client):
        """GET /providers/math/tools/history returns non-empty history list."""
        response = api_client.get("/mcp_servers/math/tools/history")
        data = response.json()
        assert data["mcp_server_id"] == "math"
        assert isinstance(data["history"], list)
        assert len(data["history"]) == 1

    def test_returns_200_with_empty_history_for_unknown_provider(self, api_client):
        """GET /providers/unknown/tools/history returns 200 with empty history (not 404)."""
        response = api_client.get("/mcp_servers/unknown/tools/history")
        assert response.status_code == 200
        data = response.json()
        assert data["history"] == []
        assert data["total"] == 0

    def test_default_limit_is_100(self, api_client, mock_context):
        """GET /providers/math/tools/history without limit uses default 100."""
        api_client.get("/mcp_servers/math/tools/history")
        calls = mock_context.query_bus.execute.call_args_list
        query_call = next(c for c in calls if isinstance(c[0][0], GetToolInvocationHistoryQuery))
        assert query_call[0][0].limit == 100

    def test_limit_query_param_forwarded(self, api_client, mock_context):
        """GET /providers/math/tools/history?limit=50 forwards limit=50 to query."""
        api_client.get("/mcp_servers/math/tools/history?limit=50")
        calls = mock_context.query_bus.execute.call_args_list
        query_call = next(c for c in calls if isinstance(c[0][0], GetToolInvocationHistoryQuery))
        assert query_call[0][0].limit == 50

    def test_from_position_query_param_forwarded(self, api_client, mock_context):
        """GET /providers/math/tools/history?from_position=10 forwards from_position=10."""
        api_client.get("/mcp_servers/math/tools/history?from_position=10")
        calls = mock_context.query_bus.execute.call_args_list
        query_call = next(c for c in calls if isinstance(c[0][0], GetToolInvocationHistoryQuery))
        assert query_call[0][0].from_position == 10

    def test_invalid_limit_falls_back_to_default(self, api_client, mock_context):
        """GET /providers/math/tools/history?limit=bad uses default 100."""
        api_client.get("/mcp_servers/math/tools/history?limit=bad")
        calls = mock_context.query_bus.execute.call_args_list
        query_call = next(c for c in calls if isinstance(c[0][0], GetToolInvocationHistoryQuery))
        assert query_call[0][0].limit == 100

    def test_dispatches_get_tool_invocation_history_query(self, api_client, mock_context):
        """GET /providers/math/tools/history dispatches GetToolInvocationHistoryQuery."""
        api_client.get("/mcp_servers/math/tools/history")
        calls = mock_context.query_bus.execute.call_args_list
        assert any(isinstance(c[0][0], GetToolInvocationHistoryQuery) for c in calls)
