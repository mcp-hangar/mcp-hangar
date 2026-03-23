"""Tests for observability REST API endpoints.

Tests cover:
- GET /observability/metrics - Prometheus text + JSON summary
- GET /observability/metrics/per-provider - Per-provider metric aggregates
- GET /observability/metrics/history - Time-series metric history
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


# ---------------------------------------------------------------------------
# GET /observability/metrics/per-provider
# ---------------------------------------------------------------------------

SAMPLE_PER_PROVIDER_TEXT = """# HELP mcp_hangar_tool_calls_total Total tool calls
# TYPE mcp_hangar_tool_calls_total counter
mcp_hangar_tool_calls_total{provider="math",tool="add",status="success"} 10.0
mcp_hangar_tool_calls_total{provider="math",tool="add",status="error"} 2.0
mcp_hangar_tool_calls_total{provider="science",tool="calc",status="success"} 5.0
# HELP mcp_hangar_tool_call_errors_total Total tool call errors
# TYPE mcp_hangar_tool_call_errors_total counter
mcp_hangar_tool_call_errors_total{provider="math",tool="add"} 2.0
# HELP mcp_hangar_health_checks_total Total health checks
# TYPE mcp_hangar_health_checks_total counter
mcp_hangar_health_checks_total{provider="math",result="healthy"} 8.0
mcp_hangar_health_checks_total{provider="math",result="unhealthy"} 1.0
# HELP mcp_hangar_provider_cold_start_seconds Cold start latency
# TYPE mcp_hangar_provider_cold_start_seconds histogram
mcp_hangar_provider_cold_start_seconds_count{provider="math"} 3.0
"""


@pytest.fixture
def per_provider_api_client(mock_audit_records, mock_security_events, mock_alerts):
    """TestClient with per-provider metrics Prometheus text mocked."""
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
        patch("mcp_hangar.server.api.observability.get_metrics", return_value=SAMPLE_PER_PROVIDER_TEXT),
        patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
        patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
        patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
    ):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client


class TestGetPerProviderMetrics:
    """Tests for GET /observability/metrics/per-provider."""

    def test_returns_200(self, per_provider_api_client):
        """GET /observability/metrics/per-provider returns HTTP 200."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        assert response.status_code == 200

    def test_returns_providers_key(self, per_provider_api_client):
        """Response contains providers list."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        assert "providers" in data
        assert isinstance(data["providers"], list)

    def test_returns_timestamp_key(self, per_provider_api_client):
        """Response contains a timestamp string."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        assert "timestamp" in data
        assert isinstance(data["timestamp"], str)

    def test_aggregates_by_provider(self, per_provider_api_client):
        """Separate entries exist for each provider found in metrics."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        provider_ids = {p["provider_id"] for p in data["providers"]}
        assert "math" in provider_ids
        assert "science" in provider_ids

    def test_tool_calls_total_summed_per_provider(self, per_provider_api_client):
        """tool_calls_total sums all labeled lines for each provider."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        math_entry = next(p for p in data["providers"] if p["provider_id"] == "math")
        # 10.0 (success) + 2.0 (error) = 12.0
        assert math_entry["tool_calls_total"] == pytest.approx(12.0)

    def test_tool_call_errors_counted(self, per_provider_api_client):
        """tool_call_errors is populated from mcp_hangar_tool_call_errors_total lines."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        math_entry = next(p for p in data["providers"] if p["provider_id"] == "math")
        assert math_entry["tool_call_errors"] == pytest.approx(2.0)

    def test_health_check_failures_counted(self, per_provider_api_client):
        """health_check_failures counts only result=unhealthy lines."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        math_entry = next(p for p in data["providers"] if p["provider_id"] == "math")
        assert math_entry["health_check_failures"] == pytest.approx(1.0)

    def test_cold_starts_counted(self, per_provider_api_client):
        """cold_starts_total is populated from histogram _count lines."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        math_entry = next(p for p in data["providers"] if p["provider_id"] == "math")
        assert math_entry["cold_starts_total"] == pytest.approx(3.0)

    def test_provider_entry_has_required_keys(self, per_provider_api_client):
        """Each provider entry contains all required metric keys."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        entry = data["providers"][0]
        for key in (
            "provider_id",
            "tool_calls_total",
            "tool_call_errors",
            "cold_starts_total",
            "health_checks_total",
            "health_check_failures",
        ):
            assert key in entry, f"Missing key: {key}"

    def test_provider_without_errors_has_zero(self, per_provider_api_client):
        """Provider with no error lines gets tool_call_errors=0."""
        response = per_provider_api_client.get("/observability/metrics/per-provider")
        data = response.json()
        science_entry = next(p for p in data["providers"] if p["provider_id"] == "science")
        assert science_entry["tool_call_errors"] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# GET /observability/metrics/history
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_metric_points():
    """Sample MetricPoint-like objects returned by the history store."""
    p1 = Mock()
    p1.provider_id = "math"
    p1.metric_name = "tool_calls_total"
    p1.value = 10.0
    p1.recorded_at = 1_700_000_000.0

    p2 = Mock()
    p2.provider_id = "science"
    p2.metric_name = "tool_calls_total"
    p2.value = 5.0
    p2.recorded_at = 1_700_000_060.0

    return [p1, p2]


@pytest.fixture
def history_api_client(mock_audit_records, mock_security_events, mock_alerts, mock_metric_points):
    """TestClient with metrics history store mocked."""
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

    mock_store = Mock()
    mock_store.query.return_value = mock_metric_points

    with (
        patch("mcp_hangar.server.api.observability.get_metrics", return_value=""),
        patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
        patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
        patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
        patch("mcp_hangar.server.api.observability.get_metrics_history_store", return_value=mock_store),
    ):
        app = create_api_router()
        client = TestClient(app, raise_server_exceptions=False)
        yield client, mock_store


class TestGetMetricsHistory:
    """Tests for GET /observability/metrics/history."""

    def test_returns_200(self, history_api_client):
        """GET /observability/metrics/history returns HTTP 200."""
        client, _ = history_api_client
        response = client.get("/observability/metrics/history")
        assert response.status_code == 200

    def test_returns_points_key(self, history_api_client):
        """Response contains points list."""
        client, _ = history_api_client
        response = client.get("/observability/metrics/history")
        data = response.json()
        assert "points" in data
        assert isinstance(data["points"], list)

    def test_returns_count_key(self, history_api_client):
        """Response contains count equal to number of points returned."""
        client, _ = history_api_client
        response = client.get("/observability/metrics/history")
        data = response.json()
        assert "count" in data
        assert data["count"] == len(data["points"])

    def test_points_have_required_fields(self, history_api_client):
        """Each point contains provider_id, metric_name, value, recorded_at."""
        client, _ = history_api_client
        response = client.get("/observability/metrics/history")
        data = response.json()
        point = data["points"][0]
        for key in ("provider_id", "metric_name", "value", "recorded_at"):
            assert key in point, f"Missing key: {key}"

    def test_provider_filter_passed_to_store(self, history_api_client):
        """provider query param is forwarded to store.query()."""
        client, mock_store = history_api_client
        client.get("/observability/metrics/history?provider=math")
        call_kwargs = mock_store.query.call_args.kwargs
        assert call_kwargs.get("provider_id") == "math"

    def test_metric_filter_passed_to_store(self, history_api_client):
        """metric query param is forwarded to store.query()."""
        client, mock_store = history_api_client
        client.get("/observability/metrics/history?metric=tool_calls_total")
        call_kwargs = mock_store.query.call_args.kwargs
        assert call_kwargs.get("metric_name") == "tool_calls_total"

    def test_from_and_to_passed_to_store(self, history_api_client):
        """from/to Unix timestamp params are forwarded as floats to store.query()."""
        client, mock_store = history_api_client
        client.get("/observability/metrics/history?from=1700000000&to=1700003600")
        call_kwargs = mock_store.query.call_args.kwargs
        assert call_kwargs.get("from_ts") == pytest.approx(1_700_000_000.0)
        assert call_kwargs.get("to_ts") == pytest.approx(1_700_003_600.0)

    def test_limit_passed_to_store(self, history_api_client):
        """limit query param is forwarded to store.query()."""
        client, mock_store = history_api_client
        client.get("/observability/metrics/history?limit=50")
        call_kwargs = mock_store.query.call_args.kwargs
        assert call_kwargs.get("limit") == 50

    def test_empty_history_returns_zero_count(self, mock_audit_records, mock_security_events, mock_alerts):
        """Empty history store returns points=[] and count=0."""
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
        mock_store = Mock()
        mock_store.query.return_value = []

        with (
            patch("mcp_hangar.server.api.observability.get_metrics", return_value=""),
            patch("mcp_hangar.server.api.observability.get_audit_handler", return_value=mock_audit_handler),
            patch("mcp_hangar.server.api.observability.get_security_handler", return_value=mock_security_handler),
            patch("mcp_hangar.server.api.observability.get_alert_handler", return_value=mock_alert_handler),
            patch("mcp_hangar.server.api.observability.get_metrics_history_store", return_value=mock_store),
        ):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/observability/metrics/history")

        assert response.status_code == 200
        data = response.json()
        assert data["points"] == []
        assert data["count"] == 0
