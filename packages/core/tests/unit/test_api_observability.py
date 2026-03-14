"""Tests for observability REST API endpoints.

Tests cover:
- GET /observability/metrics - Prometheus text + JSON summary
- GET /observability/audit - Audit log records with optional filters
- GET /observability/security - Security events
- GET /observability/alerts - Alert history with optional level filter
"""

from unittest.mock import Mock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_audit_records():
    """Sample audit records."""
    r1 = Mock()
    r1.to_dict.return_value = {
        "event_id": "abc123",
        "event_type": "ProviderStarted",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "provider_id": "math",
        "data": {},
        "recorded_at": "2026-01-01T00:00:01+00:00",
    }
    r2 = Mock()
    r2.to_dict.return_value = {
        "event_id": "def456",
        "event_type": "ToolInvocationCompleted",
        "occurred_at": "2026-01-01T00:01:00+00:00",
        "provider_id": "science",
        "data": {},
        "recorded_at": "2026-01-01T00:01:01+00:00",
    }
    return [r1, r2]


@pytest.fixture
def mock_security_events():
    """Sample security events."""
    e1 = Mock()
    e1.to_dict.return_value = {
        "event_id": "sec001",
        "event_type": "access_granted",
        "severity": "info",
        "message": "Provider started: math",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "provider_id": "math",
    }
    return [e1]


@pytest.fixture
def mock_alerts():
    """Sample alert objects."""
    a1 = Mock()
    a1.level = "warning"
    a1.to_dict.return_value = {
        "level": "warning",
        "message": "Provider degraded",
        "provider_id": "math",
        "event_type": "ProviderDegraded",
        "timestamp": "2026-01-01T00:00:00+00:00",
        "details": {},
    }
    a2 = Mock()
    a2.level = "critical"
    a2.to_dict.return_value = {
        "level": "critical",
        "message": "Provider critically degraded",
        "provider_id": "math",
        "event_type": "ProviderDegraded",
        "timestamp": "2026-01-01T00:00:05+00:00",
        "details": {},
    }
    return [a1, a2]


SAMPLE_PROMETHEUS_TEXT = """# HELP mcp_hangar_tool_calls_total Total tool calls
# TYPE mcp_hangar_tool_calls_total counter
mcp_hangar_tool_calls_total{provider="math",tool="add",status="success"} 5.0
mcp_hangar_tool_calls_total{provider="math",tool="add",status="error"} 1.0
# HELP mcp_hangar_health_checks_total Total health checks
# TYPE mcp_hangar_health_checks_total counter
mcp_hangar_health_check_total{provider="math",result="pass"} 3.0
"""


@pytest.fixture
def api_client(mock_audit_records, mock_security_events, mock_alerts):
    """Starlette TestClient for the API app with observability mocks."""
    from mcp_hangar.server.api import create_api_router

    mock_audit_handler = Mock()
    mock_audit_handler.query.return_value = mock_audit_records

    mock_security_handler = Mock()
    mock_sink = Mock()
    mock_sink.query.return_value = mock_security_events
    mock_security_handler._sink = mock_sink
    mock_security_handler.sink = mock_sink

    mock_alert_handler = Mock()
    mock_alert_handler.alerts_sent = mock_alerts

    with (
        patch("mcp_hangar.server.api.observability.get_metrics", return_value=SAMPLE_PROMETHEUS_TEXT),
        patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
        patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
        patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
    ):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


# ---------------------------------------------------------------------------
# GET /observability/metrics
# ---------------------------------------------------------------------------


class TestGetMetrics:
    """Tests for GET /observability/metrics."""

    def test_returns_200(self, api_client):
        """GET /observability/metrics returns HTTP 200."""
        response = api_client.get("/observability/metrics")
        assert response.status_code == 200

    def test_returns_prometheus_text_key(self, api_client):
        """Response contains prometheus_text key."""
        response = api_client.get("/observability/metrics")
        data = response.json()
        assert "prometheus_text" in data

    def test_returns_summary_key(self, api_client):
        """Response contains summary key."""
        response = api_client.get("/observability/metrics")
        data = response.json()
        assert "summary" in data

    def test_prometheus_text_matches_mock(self, api_client):
        """prometheus_text matches the mocked metric output."""
        response = api_client.get("/observability/metrics")
        data = response.json()
        assert data["prometheus_text"] == SAMPLE_PROMETHEUS_TEXT

    def test_summary_counts_tool_calls(self, api_client):
        """summary.tool_calls_total sums labeled counter values."""
        response = api_client.get("/observability/metrics")
        data = response.json()
        # 5.0 + 1.0 = 6.0 tool calls
        assert data["summary"].get("tool_calls_total") == pytest.approx(6.0)

    def test_summary_counts_health_checks(self, api_client):
        """summary.health_checks_total sums health check counter values."""
        response = api_client.get("/observability/metrics")
        data = response.json()
        # 3.0 health checks
        assert data["summary"].get("health_checks_total") == pytest.approx(3.0)


# ---------------------------------------------------------------------------
# GET /observability/audit
# ---------------------------------------------------------------------------


class TestGetAuditLog:
    """Tests for GET /observability/audit."""

    def test_returns_200(self, api_client):
        """GET /observability/audit returns HTTP 200."""
        response = api_client.get("/observability/audit")
        assert response.status_code == 200

    def test_returns_records_key(self, api_client):
        """Response contains records list."""
        response = api_client.get("/observability/audit")
        data = response.json()
        assert "records" in data
        assert isinstance(data["records"], list)

    def test_returns_total_key(self, api_client):
        """Response contains total count."""
        response = api_client.get("/observability/audit")
        data = response.json()
        assert "total" in data
        assert data["total"] == 2

    def test_records_have_expected_fields(self, api_client):
        """Audit records have expected fields."""
        response = api_client.get("/observability/audit")
        data = response.json()
        record = data["records"][0]
        assert "event_id" in record
        assert "event_type" in record
        assert "provider_id" in record

    def test_provider_id_filter_passed_to_handler(self, mock_audit_records, mock_security_events, mock_alerts):
        """provider_id query param is passed to handler.query()."""
        from mcp_hangar.server.api import create_api_router

        mock_audit_handler = Mock()
        mock_audit_handler.query.return_value = [mock_audit_records[0]]  # Only math provider

        mock_security_handler = Mock()
        mock_sink = Mock()
        mock_sink.query.return_value = mock_security_events
        mock_security_handler._sink = mock_sink

        mock_alert_handler = Mock()
        mock_alert_handler.alerts_sent = mock_alerts

        with (
            patch("mcp_hangar.server.api.observability.get_metrics", return_value=SAMPLE_PROMETHEUS_TEXT),
            patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
            patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
            patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
        ):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/observability/audit?provider_id=math")

        assert response.status_code == 200
        # Verify handler.query was called with provider_id=math
        mock_audit_handler.query.assert_called_once_with(provider_id="math", event_type=None, limit=100)

    def test_limit_param_is_applied(self, mock_audit_records, mock_security_events, mock_alerts):
        """limit query param is passed to handler.query()."""
        from mcp_hangar.server.api import create_api_router

        mock_audit_handler = Mock()
        mock_audit_handler.query.return_value = []

        mock_security_handler = Mock()
        mock_sink = Mock()
        mock_sink.query.return_value = []
        mock_security_handler._sink = mock_sink

        mock_alert_handler = Mock()
        mock_alert_handler.alerts_sent = []

        with (
            patch("mcp_hangar.server.api.observability.get_metrics", return_value=""),
            patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
            patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
            patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
        ):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/observability/audit?limit=50")

        assert response.status_code == 200
        mock_audit_handler.query.assert_called_once_with(provider_id=None, event_type=None, limit=50)


# ---------------------------------------------------------------------------
# GET /observability/security
# ---------------------------------------------------------------------------


class TestGetSecurityEvents:
    """Tests for GET /observability/security."""

    def test_returns_200(self, api_client):
        """GET /observability/security returns HTTP 200."""
        response = api_client.get("/observability/security")
        assert response.status_code == 200

    def test_returns_events_key(self, api_client):
        """Response contains events list."""
        response = api_client.get("/observability/security")
        data = response.json()
        assert "events" in data
        assert isinstance(data["events"], list)

    def test_returns_total_key(self, api_client):
        """Response contains total count."""
        response = api_client.get("/observability/security")
        data = response.json()
        assert "total" in data
        assert data["total"] == 1

    def test_events_have_expected_fields(self, api_client):
        """Security events have expected fields."""
        response = api_client.get("/observability/security")
        data = response.json()
        event = data["events"][0]
        assert "event_id" in event
        assert "event_type" in event


# ---------------------------------------------------------------------------
# GET /observability/alerts
# ---------------------------------------------------------------------------


class TestGetAlertHistory:
    """Tests for GET /observability/alerts."""

    def test_returns_200(self, api_client):
        """GET /observability/alerts returns HTTP 200."""
        response = api_client.get("/observability/alerts")
        assert response.status_code == 200

    def test_returns_alerts_key(self, api_client):
        """Response contains alerts list."""
        response = api_client.get("/observability/alerts")
        data = response.json()
        assert "alerts" in data
        assert isinstance(data["alerts"], list)

    def test_returns_total_key(self, api_client):
        """Response contains total count."""
        response = api_client.get("/observability/alerts")
        data = response.json()
        assert "total" in data
        assert data["total"] == 2

    def test_alerts_have_expected_fields(self, api_client):
        """Alert records have expected fields."""
        response = api_client.get("/observability/alerts")
        data = response.json()
        alert = data["alerts"][0]
        assert "level" in alert
        assert "message" in alert
        assert "provider_id" in alert

    def test_level_filter_returns_only_matching(self, mock_audit_records, mock_security_events, mock_alerts):
        """level query param filters alerts by level."""
        from mcp_hangar.server.api import create_api_router

        mock_audit_handler = Mock()
        mock_audit_handler.query.return_value = []

        mock_security_handler = Mock()
        mock_sink = Mock()
        mock_sink.query.return_value = []
        mock_security_handler._sink = mock_sink

        mock_alert_handler = Mock()
        mock_alert_handler.alerts_sent = mock_alerts  # [warning, critical]

        with (
            patch("mcp_hangar.server.api.observability.get_metrics", return_value=""),
            patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
            patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
            patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
        ):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/observability/alerts?level=critical")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["alerts"][0]["level"] == "critical"
