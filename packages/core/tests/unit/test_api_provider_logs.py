"""Tests for GET /providers/{provider_id}/logs REST endpoint (LOG-03)."""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient

from mcp_hangar.application.queries.queries import GetProviderQuery
from mcp_hangar.application.read_models.provider_views import HealthInfo, ProviderDetails, ToolInfo
from mcp_hangar.domain.exceptions import ProviderNotFoundError
from mcp_hangar.domain.value_objects.log import LogLine
from mcp_hangar.infrastructure.persistence.log_buffer import (
    ProviderLogBuffer,
    clear_log_buffer_registry,
    set_log_buffer,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_registry():
    """Clear the log buffer registry before and after each test."""
    clear_log_buffer_registry()
    yield
    clear_log_buffer_registry()


@pytest.fixture
def provider_details():
    """Sample ProviderDetails for the 'math' provider."""
    return ProviderDetails(
        provider_id="math",
        state="ready",
        mode="subprocess",
        is_alive=True,
        tools=[ToolInfo(name="add", description="Add", input_schema={})],
        health=HealthInfo(
            consecutive_failures=0,
            total_invocations=5,
            total_failures=0,
            success_rate=1.0,
            can_retry=True,
            last_success_ago=1.0,
            last_failure_ago=None,
        ),
        idle_time=0.0,
        meta={},
    )


@pytest.fixture
def mock_context(provider_details):
    """Mock ApplicationContext with a query bus that knows about 'math'."""
    ctx = Mock()
    query_bus = Mock()

    def execute_query(query):
        if isinstance(query, GetProviderQuery):
            if query.provider_id == "math":
                return provider_details
            raise ProviderNotFoundError(query.provider_id)
        raise ValueError(f"Unexpected query: {type(query)}")

    query_bus.execute.side_effect = execute_query
    ctx.query_bus = query_bus
    ctx.command_bus = Mock()
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
# Helper
# ---------------------------------------------------------------------------


def _make_lines(provider_id: str, n: int) -> list[LogLine]:
    """Create *n* LogLine objects for the given provider."""
    return [LogLine(provider_id=provider_id, stream="stderr", content=f"line-{i}") for i in range(n)]


# ---------------------------------------------------------------------------
# GET /providers/{id}/logs — happy path
# ---------------------------------------------------------------------------


class TestGetProviderLogsHappyPath:
    """Happy-path tests for GET /providers/{id}/logs."""

    def test_returns_200_when_provider_has_no_buffer(self, api_client):
        """Returns 200 with empty list when no buffer registered."""
        response = api_client.get("/providers/math/logs")
        assert response.status_code == 200

    def test_response_shape_when_no_buffer(self, api_client):
        """Response contains logs, provider_id, and count keys."""
        response = api_client.get("/providers/math/logs")
        data = response.json()
        assert data == {"logs": [], "provider_id": "math", "count": 0}

    def test_returns_200_with_buffered_lines(self, api_client):
        """Returns 200 and includes log lines when buffer is populated."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 5):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 5
        assert len(data["logs"]) == 5

    def test_log_line_has_expected_fields(self, api_client):
        """Each log entry contains provider_id, stream, content, recorded_at."""
        buf = ProviderLogBuffer("math")
        buf.append(LogLine(provider_id="math", stream="stderr", content="hello"))
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs")
        entry = response.json()["logs"][0]
        assert entry["provider_id"] == "math"
        assert entry["stream"] == "stderr"
        assert entry["content"] == "hello"
        assert isinstance(entry["recorded_at"], float)

    def test_default_lines_param_is_100(self, api_client):
        """Without ?lines=, endpoint returns up to 100 lines."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 150):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs")
        data = response.json()
        assert data["count"] == 100

    def test_lines_param_limits_result(self, api_client):
        """?lines=10 returns at most 10 lines."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 50):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=10")
        data = response.json()
        assert data["count"] == 10
        assert len(data["logs"]) == 10

    def test_lines_param_returns_most_recent(self, api_client):
        """?lines=N returns the most recent N lines (tail semantics)."""
        buf = ProviderLogBuffer("math")
        for i in range(5):
            buf.append(LogLine(provider_id="math", stream="stderr", content=f"line-{i}"))
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=3")
        contents = [e["content"] for e in response.json()["logs"]]
        assert contents == ["line-2", "line-3", "line-4"]

    def test_lines_param_exceeds_buffer_size_returns_all(self, api_client):
        """?lines=1000 returns all lines when buffer has fewer."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 20):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=1000")
        data = response.json()
        assert data["count"] == 20

    def test_provider_id_in_response(self, api_client):
        """Response always echoes back the provider_id."""
        response = api_client.get("/providers/math/logs")
        assert response.json()["provider_id"] == "math"


# ---------------------------------------------------------------------------
# GET /providers/{id}/logs — parameter clamping
# ---------------------------------------------------------------------------


class TestGetProviderLogsParamClamping:
    """Parameter validation and clamping for the lines query param."""

    def test_lines_clamped_to_max_1000(self, api_client):
        """?lines=9999 is clamped to 1000."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 1000):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=9999")
        data = response.json()
        assert data["count"] <= 1000

    def test_lines_clamped_to_min_1(self, api_client):
        """?lines=0 is clamped to 1."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 5):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=0")
        data = response.json()
        assert data["count"] == 1

    def test_invalid_lines_param_uses_default(self, api_client):
        """?lines=abc falls back to default 100."""
        buf = ProviderLogBuffer("math")
        for line in _make_lines("math", 150):
            buf.append(line)
        set_log_buffer("math", buf)

        response = api_client.get("/providers/math/logs?lines=abc")
        data = response.json()
        assert data["count"] == 100


# ---------------------------------------------------------------------------
# GET /providers/{id}/logs — 404 for unknown providers
# ---------------------------------------------------------------------------


class TestGetProviderLogsNotFound:
    """Error path: unknown provider should yield 404."""

    def test_returns_404_for_unknown_provider(self, api_client):
        """GET /providers/unknown/logs returns HTTP 404."""
        response = api_client.get("/providers/unknown/logs")
        assert response.status_code == 404

    def test_returns_error_envelope_for_unknown_provider(self, api_client):
        """Error response follows the standard envelope format."""
        response = api_client.get("/providers/unknown/logs")
        data = response.json()
        assert "error" in data
        assert data["error"]["code"] == "ProviderNotFoundError"
        assert "message" in data["error"]
