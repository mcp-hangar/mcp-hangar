"""Tests for CLI status command.

Tests cover status data collection and formatting.
"""

from pathlib import Path
from tempfile import TemporaryDirectory

from mcp_hangar.server.cli.commands.status import (
    _format_memory,
    _format_uptime,
    _get_status_from_config,
    STATE_COLORS,
    STATE_ICONS,
)


class TestStateColors:
    """Tests for state color mapping."""

    def test_all_states_have_colors(self):
        """All provider states should have color mappings."""
        expected_states = ["READY", "COLD", "INITIALIZING", "DEGRADED", "DEAD"]
        for state in expected_states:
            assert state in STATE_COLORS

    def test_all_states_have_icons(self):
        """All provider states should have icon mappings."""
        expected_states = ["READY", "COLD", "INITIALIZING", "DEGRADED", "DEAD"]
        for state in expected_states:
            assert state in STATE_ICONS


class TestFormatUptime:
    """Tests for uptime formatting."""

    def test_format_uptime_none(self):
        """None uptime should return dash."""
        assert _format_uptime(None) == "-"

    def test_format_uptime_seconds(self):
        """Short uptime should show seconds."""
        assert _format_uptime(45) == "45s"

    def test_format_uptime_minutes(self):
        """Medium uptime should show minutes."""
        assert _format_uptime(120) == "2m"
        assert _format_uptime(300) == "5m"

    def test_format_uptime_hours(self):
        """Longer uptime should show hours and minutes."""
        assert "h" in _format_uptime(3700)

    def test_format_uptime_days(self):
        """Very long uptime should show days."""
        assert "d" in _format_uptime(100000)


class TestFormatMemory:
    """Tests for memory formatting."""

    def test_format_memory_none(self):
        """None memory should return dash."""
        assert _format_memory(None) == "-"

    def test_format_memory_kb(self):
        """Small memory should show KB."""
        result = _format_memory(0.5)
        assert "KB" in result

    def test_format_memory_mb(self):
        """Larger memory should show MB."""
        result = _format_memory(128.5)
        assert "MB" in result


class TestGetStatusFromConfig:
    """Tests for getting status from config file."""

    def test_get_status_from_valid_config(self):
        """Should parse valid config file."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
providers:
  fetch:
    mode: subprocess
    command: [npx, -y, "@anthropic/mcp-server-fetch"]
  memory:
    mode: subprocess
    command: [npx, -y, "@anthropic/mcp-server-memory"]
"""
            )

            status = _get_status_from_config(config_path)

            assert status["server_running"] is False
            assert len(status["providers"]) == 2
            provider_names = [p["name"] for p in status["providers"]]
            assert "fetch" in provider_names
            assert "memory" in provider_names

    def test_get_status_providers_are_cold(self):
        """All providers should be COLD when server not running."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text(
                """
providers:
  fetch:
    mode: subprocess
"""
            )

            status = _get_status_from_config(config_path)

            assert all(p["state"] == "COLD" for p in status["providers"])

    def test_get_status_missing_config(self):
        """Should return error for missing config."""
        status = _get_status_from_config(Path("/nonexistent/config.yaml"))

        assert status["server_running"] is False
        assert "error" in status or len(status["providers"]) == 0

    def test_get_status_includes_config_path(self):
        """Should include the config path used."""
        with TemporaryDirectory() as tmpdir:
            config_path = Path(tmpdir) / "config.yaml"
            config_path.write_text("providers: {}")

            status = _get_status_from_config(config_path)

            assert "config_path" in status
