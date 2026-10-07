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


@pytest.fixture(autouse=True)
def the_named_files_exist(monkeypatch):
    """These pin which path `serve` passes on; the paths are fictional.

    `serve` refuses a file that does not exist (#1650), which
    `test_a_missing_config_file_is_fatal.py` covers.
    """
    from mcp_hangar.server.cli.commands import serve

    monkeypatch.setattr(serve, "require_config_file", lambda resolved: resolved.path)


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

    @pytest.mark.parametrize("argv", [[], ["serve"]])
    @pytest.mark.parametrize("mode", ["http", "true", "1", "yes"])
    def test_serve_envvars_apply_to_default_and_explicit_command(self, runner, argv, mode):
        """The implicit default command must resolve envvars like `serve` does."""
        from mcp_hangar.server.cli.main import app

        env = {
            "MCP_MODE": mode,
            "MCP_HTTP_HOST": "127.0.0.1",
            "MCP_HTTP_PORT": "9099",
            "MCP_LOG_LEVEL": "debug",
            "MCP_JSON_LOGS": "1",
        }

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, argv, env=env)

        assert result.exit_code == 0, result.output
        cli_config = mock_run.call_args.args[0]
        assert cli_config.http_mode is True
        assert cli_config.http_host == "127.0.0.1"
        assert cli_config.http_port == 9099
        assert cli_config.log_level == "DEBUG"
        assert cli_config.json_logs is True

    @pytest.mark.parametrize("argv", [[], ["serve"]])
    def test_invalid_port_env_exits_cleanly(self, runner, argv):
        """Both default and explicit serve reject an invalid env port cleanly."""
        from mcp_hangar.server.cli.main import app

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, argv, env={"MCP_HTTP_PORT": "abc"})

        assert result.exit_code == 2
        assert not mock_run.called


class TestGeneratedClientEntryStarts:
    """The args written into a client's config must actually start the server."""

    def test_generated_args_start_the_server(self, runner, tmp_path, monkeypatch):
        """Feeding a written client entry to the CLI resolves the config and starts."""
        import json
        from pathlib import Path

        from mcp_hangar.server.cli.main import app
        from mcp_hangar.server.cli.services.mcp_clients import (
            HANGAR_ENTRY_NAME,
            client_by_key,
            write_hangar_entry,
        )

        monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
        client = client_by_key("cursor", cwd=tmp_path)
        write_hangar_entry(client, Path("/tmp/hangar.yaml"))
        args = json.loads(client.path.read_text())["mcpServers"][HANGAR_ENTRY_NAME]["args"]

        with patch("mcp_hangar.server.lifecycle.run_server") as mock_run:
            result = runner.invoke(app, args)

        assert result.exit_code == 0, result.output
        assert "No such option" not in result.output
        assert _config_path_from_run_server(mock_run) == "/tmp/hangar.yaml"
