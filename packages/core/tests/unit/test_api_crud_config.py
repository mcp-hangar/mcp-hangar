"""Tests for config export and backup REST API endpoints.

Tests cover:
- POST /config/export  -> 200 {"yaml": "<full config as YAML string>"}
- POST /config/backup  -> 200 {"path": "<backup file path>"}
"""

from unittest.mock import Mock, patch

import yaml

from mcp_hangar.server.api import create_api_router


# ---------------------------------------------------------------------------
# Fixtures helpers
# ---------------------------------------------------------------------------


def _make_client():
    """Create a Starlette TestClient for the API app with mocked context."""
    from starlette.testclient import TestClient

    mock_context = Mock()
    mock_context.runtime = None
    mock_context.groups = {}

    with patch("mcp_hangar.server.api.middleware.get_context", return_value=mock_context):
        with patch("mcp_hangar.server.api.config.get_context", return_value=mock_context):
            app = create_api_router()
            client = TestClient(app, raise_server_exceptions=False)
            yield client


# ---------------------------------------------------------------------------
# POST /config/export
# ---------------------------------------------------------------------------


class TestExportConfig:
    """Tests for POST /config/export endpoint."""

    def test_returns_200_on_success(self):
        """POST /config/export returns HTTP 200."""
        sample_config = {"providers": {"math": {"mode": "subprocess", "command": ["python", "-m", "math"]}}}

        with patch(
            "mcp_hangar.server.api.config.serialize_full_config",
            return_value=sample_config,
        ):
            for client in _make_client():
                response = client.post("/config/export")
        assert response.status_code == 200

    def test_returns_yaml_key(self):
        """POST /config/export response contains 'yaml' key."""
        sample_config = {"providers": {}}

        with patch(
            "mcp_hangar.server.api.config.serialize_full_config",
            return_value=sample_config,
        ):
            for client in _make_client():
                response = client.post("/config/export")
        assert "yaml" in response.json()

    def test_yaml_is_parseable(self):
        """POST /config/export yaml value can be parsed back to dict."""
        sample_config = {"providers": {"test": {"mode": "remote", "endpoint": "http://localhost:8080"}}}

        with patch(
            "mcp_hangar.server.api.config.serialize_full_config",
            return_value=sample_config,
        ):
            for client in _make_client():
                response = client.post("/config/export")

        yaml_str = response.json()["yaml"]
        parsed = yaml.safe_load(yaml_str)
        assert parsed == sample_config

    def test_yaml_matches_serialize_full_config_output(self):
        """POST /config/export yaml serialises the dict returned by serialize_full_config."""
        sample_config = {"providers": {"math": {"mode": "subprocess"}}, "logging": {"level": "INFO"}}

        with patch(
            "mcp_hangar.server.api.config.serialize_full_config",
            return_value=sample_config,
        ):
            for client in _make_client():
                response = client.post("/config/export")

        parsed = yaml.safe_load(response.json()["yaml"])
        assert parsed["providers"]["math"]["mode"] == "subprocess"
        assert parsed["logging"]["level"] == "INFO"

    def test_calls_serialize_full_config(self):
        """POST /config/export calls serialize_full_config() exactly once."""
        with patch(
            "mcp_hangar.server.api.config.serialize_full_config",
            return_value={},
        ) as mock_serialize:
            for client in _make_client():
                client.post("/config/export")

        mock_serialize.assert_called_once()


# ---------------------------------------------------------------------------
# POST /config/backup
# ---------------------------------------------------------------------------


class TestBackupConfig:
    """Tests for POST /config/backup endpoint."""

    def test_returns_200_on_success(self):
        """POST /config/backup returns HTTP 200."""
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value="/tmp/config.yaml.bak1",
        ):
            for client in _make_client():
                response = client.post("/config/backup")
        assert response.status_code == 200

    def test_returns_path_key(self):
        """POST /config/backup response contains 'path' key."""
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value="/tmp/config.yaml.bak1",
        ):
            for client in _make_client():
                response = client.post("/config/backup")
        assert "path" in response.json()

    def test_path_matches_write_config_backup_return_value(self):
        """POST /config/backup path in response is what write_config_backup returned."""
        expected_path = "/data/config.yaml.bak2"
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value=expected_path,
        ):
            for client in _make_client():
                response = client.post("/config/backup")
        assert response.json()["path"] == expected_path

    def test_calls_write_config_backup(self):
        """POST /config/backup calls write_config_backup() exactly once."""
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value="/tmp/cfg.bak1",
        ) as mock_backup:
            for client in _make_client():
                client.post("/config/backup")

        mock_backup.assert_called_once()

    def test_accepts_empty_body(self):
        """POST /config/backup succeeds with no JSON body."""
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value="/tmp/cfg.bak1",
        ):
            for client in _make_client():
                response = client.post("/config/backup")
        assert response.status_code == 200

    def test_uses_config_path_from_body_when_provided(self):
        """POST /config/backup passes config_path from request body to write_config_backup."""
        custom_path = "/etc/mcp/custom.yaml"
        with patch(
            "mcp_hangar.server.api.config.write_config_backup",
            return_value=f"{custom_path}.bak1",
        ) as mock_backup:
            for client in _make_client():
                client.post("/config/backup", json={"config_path": custom_path})

        args, _ = mock_backup.call_args
        assert args[0] == custom_path
