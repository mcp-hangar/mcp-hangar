"""Tests for config and system REST API endpoints.

Tests cover:
- GET /config returns current configuration dict
- POST /config/reload dispatches ReloadConfigurationCommand and returns result
- POST /config/reload accepts optional JSON body (config_path, graceful)
- GET /system returns system metrics with uptime_seconds and version
- Router mounts /groups, /discovery, /config, /system alongside /providers
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest
from starlette.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_system_metrics() -> Mock:
    """Create a mock SystemMetrics read model."""
    metrics = Mock()
    metrics.to_dict.return_value = {
        "total_providers": 3,
        "providers_by_state": {"ready": 2, "cold": 1},
        "total_tools": 12,
        "total_invocations": 500,
        "total_failures": 5,
        "overall_success_rate": 0.99,
    }
    return metrics


@pytest.fixture
def mock_context():
    """Mock ApplicationContext for config and system endpoints."""
    ctx = Mock()
    ctx.command_bus = Mock()
    ctx.query_bus = Mock()
    # runtime.config_repository is optional -- None by default for basic tests
    ctx.runtime = Mock()
    ctx.runtime.config_repository = None
    return ctx


@pytest.fixture
def api_client(mock_context):
    """Starlette TestClient with mocked context and dispatch helpers."""
    from mcp_hangar.server.api import create_api_router

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.config.get_context", return_value=mock_context):
            with patch("mcp_hangar.server.api.system.get_context", return_value=mock_context):
                app = create_api_router()
                client = TestClient(app, raise_server_exceptions=False)
                yield client


# ---------------------------------------------------------------------------
# GET /config
# ---------------------------------------------------------------------------


class TestGetConfig:
    """Tests for GET /config."""

    def test_returns_200(self, api_client):
        """GET /config returns HTTP 200."""
        response = api_client.get("/config/")
        assert response.status_code == 200

    def test_returns_config_key(self, api_client):
        """GET /config returns JSON with 'config' key."""
        response = api_client.get("/config/")
        data = response.json()
        assert "config" in data

    def test_config_is_dict(self, api_client):
        """GET /config returns a dict under 'config' key."""
        response = api_client.get("/config/")
        data = response.json()
        assert isinstance(data["config"], dict)

    def test_secrets_not_in_response(self, api_client, mock_context):
        """GET /config does not expose secret keys in the response."""
        response = api_client.get("/config/")
        data = response.json()
        config = data["config"]
        # Sensitive key names must not appear in the response
        sensitive_keys = {"secret", "key", "token", "password"}
        for k in config:
            assert k.lower() not in sensitive_keys, f"Sensitive key leaked: {k}"


# ---------------------------------------------------------------------------
# POST /config/reload
# ---------------------------------------------------------------------------


class TestReloadConfig:
    """Tests for POST /config/reload."""

    def test_returns_200(self, api_client):
        """POST /config/reload returns HTTP 200."""
        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value={"providers_loaded": 2},
        ):
            response = api_client.post("/config/reload")
            assert response.status_code == 200

    def test_returns_status_and_result(self, api_client):
        """POST /config/reload returns status reloaded and result."""
        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value={"providers_loaded": 2},
        ):
            response = api_client.post("/config/reload")
            data = response.json()
            assert data["status"] == "reloaded"
            assert "result" in data

    def test_dispatches_reload_command(self, api_client):
        """POST /config/reload dispatches ReloadConfigurationCommand."""
        from mcp_hangar.application.commands.commands import ReloadConfigurationCommand

        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_dispatch:
            api_client.post("/config/reload")
            mock_dispatch.assert_called_once()
            cmd = mock_dispatch.call_args[0][0]
            assert isinstance(cmd, ReloadConfigurationCommand)

    def test_accepts_config_path_in_body(self, api_client):
        """POST /config/reload accepts config_path JSON body param."""
        from mcp_hangar.application.commands.commands import ReloadConfigurationCommand

        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_dispatch:
            api_client.post("/config/reload", json={"config_path": "/etc/mcp/config.yaml"})
            cmd = mock_dispatch.call_args[0][0]
            assert isinstance(cmd, ReloadConfigurationCommand)
            assert cmd.config_path == "/etc/mcp/config.yaml"

    def test_accepts_graceful_in_body(self, api_client):
        """POST /config/reload accepts graceful JSON body param."""
        from mcp_hangar.application.commands.commands import ReloadConfigurationCommand

        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_dispatch:
            api_client.post("/config/reload", json={"graceful": False})
            cmd = mock_dispatch.call_args[0][0]
            assert isinstance(cmd, ReloadConfigurationCommand)
            assert cmd.graceful is False

    def test_defaults_graceful_to_true(self, api_client):
        """POST /config/reload defaults graceful to True when not specified."""
        from mcp_hangar.application.commands.commands import ReloadConfigurationCommand

        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_dispatch:
            api_client.post("/config/reload")
            cmd = mock_dispatch.call_args[0][0]
            assert isinstance(cmd, ReloadConfigurationCommand)
            assert cmd.graceful is True

    def test_works_with_empty_body(self, api_client):
        """POST /config/reload works when no JSON body is provided."""
        with patch(
            "mcp_hangar.server.api.config.dispatch_command",
            new_callable=AsyncMock,
            return_value=None,
        ):
            response = api_client.post("/config/reload")
            assert response.status_code == 200


# ---------------------------------------------------------------------------
# GET /system
# ---------------------------------------------------------------------------


class TestGetSystem:
    """Tests for GET /system."""

    def test_returns_200(self, api_client):
        """GET /system returns HTTP 200."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            assert response.status_code == 200

    def test_returns_system_key(self, api_client):
        """GET /system returns JSON with 'system' key."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            data = response.json()
            assert "system" in data

    def test_returns_provider_counts(self, api_client):
        """GET /system returns total_providers and providers_by_state."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            data = response.json()
            system = data["system"]
            assert system["total_providers"] == 3
            assert "providers_by_state" in system

    def test_returns_uptime_seconds(self, api_client):
        """GET /system returns uptime_seconds field."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            data = response.json()
            assert "uptime_seconds" in data["system"]
            assert isinstance(data["system"]["uptime_seconds"], (int, float))
            assert data["system"]["uptime_seconds"] >= 0

    def test_returns_version(self, api_client):
        """GET /system returns version field."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            data = response.json()
            assert "version" in data["system"]
            assert isinstance(data["system"]["version"], str)

    def test_dispatches_system_metrics_query(self, api_client):
        """GET /system dispatches GetSystemMetricsQuery."""
        from mcp_hangar.application.queries.queries import GetSystemMetricsQuery

        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ) as mock_dispatch:
            api_client.get("/system/")
            mock_dispatch.assert_called_once()
            query = mock_dispatch.call_args[0][0]
            assert isinstance(query, GetSystemMetricsQuery)


# ---------------------------------------------------------------------------
# Router integration: all routes coexist
# ---------------------------------------------------------------------------


class TestRouterIntegration:
    """Tests verifying all sub-routers are mounted and coexist."""

    def test_providers_routes_still_accessible(self, api_client):
        """Existing /providers routes still accessible after adding new sub-routers."""
        # /providers/ is mounted from Plan 11-01 -- should still return 200
        with patch(
            "mcp_hangar.server.api.middleware.dispatch_query",
            new_callable=AsyncMock,
            return_value=[],
        ):
            response = api_client.get("/providers/")
            # Just verify we don't get a 404 routing error
            assert response.status_code != 404

    def test_groups_routes_accessible(self, api_client, mock_context):
        """GET /groups/ is accessible from the router."""
        mock_context.groups = {}
        with patch("mcp_hangar.server.api.groups.get_context", return_value=mock_context):
            response = api_client.get("/groups/")
            assert response.status_code == 200

    def test_discovery_routes_accessible(self, api_client, mock_context):
        """GET /discovery/pending is accessible from the router."""
        mock_context.discovery_orchestrator = None
        with patch("mcp_hangar.server.api.discovery.get_context", return_value=mock_context):
            response = api_client.get("/discovery/pending")
            assert response.status_code == 404  # DiscoveryNotConfigured -> 404

    def test_config_route_accessible(self, api_client):
        """GET /config/ is accessible from the router."""
        response = api_client.get("/config/")
        assert response.status_code == 200

    def test_system_route_accessible(self, api_client):
        """GET /system/ is accessible from the router."""
        with patch(
            "mcp_hangar.server.api.system.dispatch_query",
            new_callable=AsyncMock,
            return_value=_make_system_metrics(),
        ):
            response = api_client.get("/system/")
            assert response.status_code == 200
