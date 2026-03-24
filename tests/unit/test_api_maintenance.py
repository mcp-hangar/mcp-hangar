"""Tests for maintenance REST API endpoints.

Tests cover:
- POST /maintenance/compact - Event stream compaction
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_context(deleted: int = 5):
    """Build a minimal mock ApplicationContext for the compact endpoint.

    Args:
        deleted: Number of events the mock event store reports as deleted.

    Returns:
        Mock ApplicationContext with the event store wired up.
    """
    mock_event_store = Mock()
    mock_event_store.compact_stream.return_value = deleted

    mock_event_bus = Mock()
    mock_event_bus.event_store = mock_event_store

    mock_runtime = Mock()
    mock_runtime.event_bus = mock_event_bus

    ctx = Mock()
    ctx.runtime = mock_runtime
    return ctx


@pytest.fixture
def api_client():
    """Starlette TestClient with maintenance routes and a mock context."""
    from mcp_hangar.server.api import create_api_router

    ctx = _make_mock_context(deleted=7)

    with patch("mcp_hangar.server.api.maintenance.get_context", return_value=ctx):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


# ---------------------------------------------------------------------------
# POST /maintenance/compact
# ---------------------------------------------------------------------------


class TestCompactStream:
    """Tests for POST /maintenance/compact."""

    def test_returns_200_on_success(self, api_client):
        """POST /maintenance/compact returns HTTP 200 when compaction succeeds."""
        response = api_client.post("/maintenance/compact", json={"stream_id": "provider:math"})
        assert response.status_code == 200

    def test_returns_compacted_key(self, api_client):
        """Response contains top-level compacted dict."""
        response = api_client.post("/maintenance/compact", json={"stream_id": "provider:math"})
        data = response.json()
        assert "compacted" in data
        assert isinstance(data["compacted"], dict)

    def test_compacted_contains_stream_id(self, api_client):
        """compacted.stream_id echoes the requested stream."""
        response = api_client.post("/maintenance/compact", json={"stream_id": "provider:math"})
        data = response.json()
        assert data["compacted"]["stream_id"] == "provider:math"

    def test_compacted_contains_events_deleted(self, api_client):
        """compacted.events_deleted matches what the event store returned."""
        response = api_client.post("/maintenance/compact", json={"stream_id": "provider:math"})
        data = response.json()
        assert data["compacted"]["events_deleted"] == 7

    def test_missing_stream_id_returns_422(self, api_client):
        """Omitting stream_id returns HTTP 422 (ValidationError)."""
        response = api_client.post("/maintenance/compact", json={})
        assert response.status_code == 422

    def test_empty_stream_id_returns_422(self, api_client):
        """Empty string stream_id returns HTTP 422."""
        response = api_client.post("/maintenance/compact", json={"stream_id": "   "})
        assert response.status_code == 422

    def test_compaction_error_returns_500(self):
        """CompactionError from the event store is mapped to HTTP 500."""
        from mcp_hangar.domain.exceptions import CompactionError
        from mcp_hangar.server.api import create_api_router

        mock_event_store = Mock()
        mock_event_store.compact_stream.side_effect = CompactionError(
            "provider:math", "no snapshot exists; create a snapshot before compacting"
        )
        mock_event_bus = Mock()
        mock_event_bus.event_store = mock_event_store
        mock_runtime = Mock()
        mock_runtime.event_bus = mock_event_bus
        ctx = Mock()
        ctx.runtime = mock_runtime

        with patch("mcp_hangar.server.api.maintenance.get_context", return_value=ctx):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/maintenance/compact", json={"stream_id": "provider:math"})

        assert response.status_code == 500

    def test_compaction_error_response_has_error_key(self):
        """CompactionError response body contains an error envelope."""
        from mcp_hangar.domain.exceptions import CompactionError
        from mcp_hangar.server.api import create_api_router

        mock_event_store = Mock()
        mock_event_store.compact_stream.side_effect = CompactionError(
            "provider:math", "no snapshot exists; create a snapshot before compacting"
        )
        mock_event_bus = Mock()
        mock_event_bus.event_store = mock_event_store
        mock_runtime = Mock()
        mock_runtime.event_bus = mock_event_bus
        ctx = Mock()
        ctx.runtime = mock_runtime

        with patch("mcp_hangar.server.api.maintenance.get_context", return_value=ctx):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/maintenance/compact", json={"stream_id": "provider:math"})

        data = response.json()
        assert "error" in data

    def test_event_store_compact_stream_called_with_correct_id(self):
        """compact_stream() is called with the exact stream_id from the request body."""
        from mcp_hangar.server.api import create_api_router

        ctx = _make_mock_context(deleted=0)

        with patch("mcp_hangar.server.api.maintenance.get_context", return_value=ctx):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            client.post("/maintenance/compact", json={"stream_id": "provider:science"})

        ctx.runtime.event_bus.event_store.compact_stream.assert_called_once_with("provider:science")

    def test_zero_events_deleted_is_valid(self):
        """A response with events_deleted=0 is valid (stream was already compact)."""
        from mcp_hangar.server.api import create_api_router

        ctx = _make_mock_context(deleted=0)

        with patch("mcp_hangar.server.api.maintenance.get_context", return_value=ctx):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.post("/maintenance/compact", json={"stream_id": "provider:math"})

        assert response.status_code == 200
        data = response.json()
        assert data["compacted"]["events_deleted"] == 0
