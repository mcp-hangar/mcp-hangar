"""Tests for the `serve` command accepting `--config`/`-c`.

Regression coverage for the CLI bug where `--config` was declared only on the
top-level callback, so `mcp-hangar serve --config X` failed with
"No such option: --config", and the generated Claude Desktop entry
(`["serve", "--config", path]`) refused to start.
"""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner():
    """Create a CLI runner."""
    return CliRunner()


def _config_path_from_run_server(mock_run_server):
    """Extract config_path from the CLIConfig passed to run_server."""
    assert mock_run_server.called, "run_server was not invoked"
    cli_config = mock_run_server.call_args.args[0]
    return cli_config.config_path


class TestServeConfigOption:
    """`serve` must accept --config both before and after the subcommand."""

    def test_serve_accepts_config_after_subcommand(self, runner):
        """`mcp-hangar serve --config X` no longer errors on the option."""
        from mcp_hangar.server.cli.main import app

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, ["serve", "--config", "/tmp/config.yaml"])

        assert result.exit_code == 0, result.output
        assert "No such option" not in result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/config.yaml"

    def test_serve_accepts_config_short_flag(self, runner):
        """`mcp-hangar serve -c X` also works via the short flag."""
        from mcp_hangar.server.cli.main import app

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, ["serve", "-c", "/tmp/short.yaml"])

        assert result.exit_code == 0, result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/short.yaml"

    def test_global_config_before_subcommand_still_works(self, runner):
        """`mcp-hangar --config X serve` continues to work (fallback path)."""
        from mcp_hangar.server.cli.main import app

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, ["--config", "/tmp/global.yaml", "serve"])

        assert result.exit_code == 0, result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/global.yaml"

    def test_serve_config_overrides_global(self, runner):
        """A --config on `serve` overrides the top-level --config."""
        from mcp_hangar.server.cli.main import app

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(
                app,
                ["--config", "/tmp/global.yaml", "serve", "--config", "/tmp/local.yaml"],
            )

        assert result.exit_code == 0, result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/local.yaml"


class TestGeneratedClaudeDesktopEntryStarts:
    """The generated Claude Desktop args must actually start the server."""

    def test_generated_args_start_the_server(self, runner):
        """Feeding the generated args to the CLI resolves the config and starts."""
        from pathlib import Path

        from mcp_hangar.server.cli.main import app
        from mcp_hangar.server.cli.services import ClaudeDesktopManager

        manager = ClaudeDesktopManager()
        entry = manager._generate_hangar_entry(Path("/tmp/hangar.yaml"))
        args = entry["mcp-hangar"]["args"]

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, args)

        assert result.exit_code == 0, result.output
        assert "No such option" not in result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/hangar.yaml"
