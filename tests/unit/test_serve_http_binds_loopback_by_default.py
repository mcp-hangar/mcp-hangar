"""`serve --http` binds loopback unless told otherwise (#1661).

The default host was `0.0.0.0`, which the gateway refuses to serve without auth,
so the help example `serve --http --port 8000` exited 1 on a fresh install.
"""

from __future__ import annotations

from unittest.mock import patch

from typer.testing import CliRunner

from mcp_hangar.server.cli import app
from mcp_hangar.server.lifecycle import _is_loopback_host

runner = CliRunner()


def _hosts(tmp_path, monkeypatch, *flags: str, env: str | None = None) -> str:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    monkeypatch.delenv("MCP_HTTP_HOST", raising=False)
    if env:
        monkeypatch.setenv("MCP_HTTP_HOST", env)
    seen: list[str] = []
    with patch("mcp_hangar.server.lifecycle.run_server", side_effect=lambda c: seen.append(c.http_host)):
        result = runner.invoke(app, ["--config", str(config), "serve", "--http", "--port", "8000", *flags])
    assert result.exit_code == 0, result.output
    return seen[0]


def test_the_help_example_binds_a_loopback_host(tmp_path, monkeypatch):
    assert _is_loopback_host(_hosts(tmp_path, monkeypatch))


def test_a_non_loopback_host_stays_an_explicit_choice(tmp_path, monkeypatch):
    assert _hosts(tmp_path, monkeypatch, "--host", "0.0.0.0") == "0.0.0.0"
    assert _hosts(tmp_path, monkeypatch, env="10.0.0.5") == "10.0.0.5"
