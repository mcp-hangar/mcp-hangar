"""A secret ``add`` prompts for is not written into ``config.yaml``, and the files the CLI creates are 0600 (#1653).

``add`` stored the prompted value under ``env`` in plaintext in a file created
0644, while the documentation said secrets are stored securely. The entry now
reads the variable (``${NAME}``) and the CLI says where to set it.
"""

from __future__ import annotations

import os
import stat
from unittest.mock import patch

import yaml
from typer.testing import CliRunner

from mcp_hangar.server.cli import app
from mcp_hangar.server.cli.services import ConfigFileManager, McpServerDefinition
from mcp_hangar.server.cli.services.config_file import write_private

SECRET = "s3cr3t-value-that-must-not-land-on-disk"


def _server() -> McpServerDefinition:
    return McpServerDefinition(
        name="tokened",
        description="needs a token",
        package="@scope/server-tokened",
        category="Test",
        requires_config=True,
        config_type="secret",
        env_var="EXAMPLE_TOKEN",
    )


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_a_prompted_secret_is_written_as_a_reference_not_as_the_value(tmp_path):
    path = tmp_path / "config.yaml"

    ConfigFileManager(path).add_mcp_server(_server(), config_value=SECRET)

    assert SECRET not in path.read_text()
    assert yaml.safe_load(path.read_text())["mcp_servers"]["tokened"]["env"] == {"EXAMPLE_TOKEN": "${EXAMPLE_TOKEN}"}


def test_the_add_command_says_where_the_secret_goes_and_does_not_echo_it(tmp_path):
    path = tmp_path / "config.yaml"
    answers = {"value": SECRET, "env_var": "EXAMPLE_TOKEN", "config_type": "secret"}
    with (
        patch("mcp_hangar.server.cli.commands.add.get_mcp_server", return_value=_server()),
        patch("mcp_hangar.server.cli.commands.add._collect_config", return_value=answers),
        patch("mcp_hangar.server.cli.commands.add.questionary.confirm") as confirm,
    ):
        confirm.return_value.ask.return_value = True
        result = CliRunner().invoke(app, ["--config", str(path), "add", "tokened", "--no-reload"])

    assert result.exit_code == 0, result.output
    assert SECRET not in result.output and SECRET not in path.read_text()
    assert "EXAMPLE_TOKEN" in result.output and "not written" in result.output


def test_a_config_the_cli_creates_is_readable_by_its_owner_only(tmp_path):
    previous = os.umask(0o022)
    try:
        manager = ConfigFileManager(tmp_path / "state" / "config.yaml")
        manager.add_mcp_server(_server(), use_env="EXAMPLE_TOKEN")
        initial = ConfigFileManager(tmp_path / "other" / "config.yaml")
        initial.write_initial_config([_server()], {})
    finally:
        os.umask(previous)

    assert _mode(manager.config_path) == 0o600
    assert _mode(initial.config_path) == 0o600


def test_a_file_that_exists_keeps_the_mode_its_owner_gave_it(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("mcp_servers: {}\n")
    path.chmod(0o640)

    write_private(path, "mcp_servers: {}\n")

    assert _mode(path) == 0o640
