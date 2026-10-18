"""A bare ``mcp-hangar`` reads the same environment ``mcp-hangar serve`` does (#1651).

The implicit serve called the command's function with its Python defaults, so
``MCP_HTTP_HOST``, ``MCP_HTTP_PORT`` and ``MCP_LOG_LEVEL`` were ignored: an
operator who asked for one interface got another.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli import app

runner = CliRunner()
ENV = {
    "MCP_HTTP_HOST": "10.1.2.3",
    "MCP_HTTP_PORT": "9123",
    "MCP_LOG_LEVEL": "debug",
    "MCP_JSON_LOGS": "true",
}


def _config_seen(tmp_path, monkeypatch, args: list[str]):
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    seen: list = []
    with patch("mcp_hangar.server.lifecycle.run_server", side_effect=seen.append):
        result = runner.invoke(app, ["--config", str(config), *args])
    assert result.exit_code == 0, result.output
    return seen[0]


@pytest.mark.parametrize("args", [[], ["serve"]], ids=["bare", "serve"])
def test_the_environment_reaches_the_server_on_both_spellings(tmp_path, monkeypatch, args):
    seen = _config_seen(tmp_path, monkeypatch, args)

    assert (seen.http_host, seen.http_port, seen.log_level, seen.json_logs) == ("10.1.2.3", 9123, "DEBUG", True)


def test_the_global_config_still_reaches_a_bare_command(tmp_path, monkeypatch):
    seen = _config_seen(tmp_path, monkeypatch, [])

    assert seen.config_path == str(tmp_path / "config.yaml")


def test_a_bare_command_with_nothing_set_keeps_the_defaults(tmp_path, monkeypatch):
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    for key in ENV:
        monkeypatch.delenv(key, raising=False)
    seen: list = []
    with patch("mcp_hangar.server.lifecycle.run_server", side_effect=seen.append):
        assert runner.invoke(app, ["--config", str(config)]).exit_code == 0

    assert (seen[0].http_host, seen[0].http_port, seen[0].log_level) == ("127.0.0.1", 8000, "INFO")
