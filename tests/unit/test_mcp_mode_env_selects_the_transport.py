"""`MCP_MODE=http` serves HTTP, as the docs and examples say (#1660).

The `--http` option is a bool bound to `MCP_MODE`, so the documented value
`http` was refused with exit 2.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli import app

runner = CliRunner()


def _serve(tmp_path, monkeypatch, mode: str | None, *flags: str) -> tuple[int, bool | None]:
    config = tmp_path / "config.yaml"
    config.write_text("mcp_servers: {}\n")
    if mode is None:
        monkeypatch.delenv("MCP_MODE", raising=False)
    else:
        monkeypatch.setenv("MCP_MODE", mode)
    seen: list[bool] = []
    with patch("mcp_hangar.server.lifecycle.run_server", side_effect=lambda c: seen.append(c.http_mode)):
        result = runner.invoke(app, ["--config", str(config), "serve", *flags])
    return result.exit_code, (seen[0] if seen else None)


@pytest.mark.parametrize("mode", ["http", "HTTP", "true", "1"])
def test_http_spellings_serve_http(tmp_path, monkeypatch, mode):
    assert _serve(tmp_path, monkeypatch, mode) == (0, True)


@pytest.mark.parametrize("mode", [None, "stdio", ""])
def test_stdio_and_unset_serve_stdio(tmp_path, monkeypatch, mode):
    assert _serve(tmp_path, monkeypatch, mode) == (0, False)


def test_the_flag_wins_over_a_stdio_env(tmp_path, monkeypatch):
    assert _serve(tmp_path, monkeypatch, "stdio", "--http") == (0, True)
