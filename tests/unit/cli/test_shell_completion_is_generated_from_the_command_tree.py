"""Shell completion names every command the CLI has, and only options it has (#1679).

The three scripts were written out by hand and drifted: they omitted `pin`, `auth`
and `config`, offered `init --MCP servers` (an option that never existed after the
provider -> mcp_server rename), and listed servers the registry no longer offers.
They are now the scripts Click generates for the real command tree, and the
completion that runs when the shell asks is read from the same tree.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from typer.main import get_command
from typer.testing import CliRunner

from mcp_hangar.server.cli import app
from mcp_hangar.server.cli.commands.completion import COMPLETE_VAR, PROG_NAME, SHELLS, completion_script

runner = CliRunner()
CLI_SOURCE = Path(__file__).resolve().parents[3] / "src" / "mcp_hangar" / "server" / "cli"


def _commands() -> set[str]:
    return set(get_command(app).commands)


def _complete(words: str, cword: int) -> list[str]:
    env = dict(os.environ, **{COMPLETE_VAR: "complete_bash", "COMP_WORDS": words, "COMP_CWORD": str(cword)})
    out = subprocess.run(
        [str(Path(sys.executable).parent / PROG_NAME)],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    ).stdout
    return out.split()


@pytest.mark.parametrize("shell", SHELLS)
def test_every_shell_gets_a_script_that_asks_the_cli(shell):
    script = completion_script(shell)

    assert COMPLETE_VAR in script
    assert PROG_NAME in script


@pytest.mark.parametrize("shell", SHELLS)
def test_the_subcommand_prints_the_script(shell):
    result = runner.invoke(app, ["completion", shell])

    assert result.exit_code == 0
    assert COMPLETE_VAR in result.stdout


def test_the_completion_that_runs_offers_every_command_the_cli_has():
    offered = set(_complete(f"{PROG_NAME} ", 1))

    assert {"pin", "auth", "config"} <= offered
    assert _commands() <= offered


def test_the_completion_that_runs_offers_the_options_init_has_and_not_the_ones_it_does_not():
    offered = set(_complete(f"{PROG_NAME} init --", 2))

    assert {"--bundle", "--servers", "--config-path", "--reset"} <= offered
    assert "--MCP" not in offered and not any("MCP servers" in o for o in offered)


def test_no_help_text_carries_rename_damage():
    """`init --MCP servers` and `status my-MCP server` were find-and-replace casualties."""
    damaged = re.compile(r"--MCP\b|[a-z0-9]-MCP server")
    hits = [
        f"{path.relative_to(CLI_SOURCE)}:{n}: {line.strip()}"
        for path in CLI_SOURCE.rglob("*.py")
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if damaged.search(line)
    ]
    assert not hits, hits
