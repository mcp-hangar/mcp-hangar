"""No `mcp_server` reaches a user through the CLI (#1195).

`mcp-hangar --help` said "Production-grade MCP mcp_server platform", `status`
offered to "Show status of all mcp_servers", and `add` would "Add a mcp_server".
That spelling is an internal identifier -- the rename from `provider` left it in
every string it had touched -- and a user has no reason to know it exists.

The gate walks the real Typer app and reads what it renders, rather than
grepping the source: a help string can be built from a constant, a docstring, or
a decorator argument, and only the rendered text is what someone actually sees.

`mcp_servers` stays where it is a name and not prose: the configuration section,
the `--mcp_servers` alias kept for scripts that already pass it, and every
identifier in the code.
"""

import re

import pytest
from typer.testing import CliRunner

from mcp_hangar.server.cli.main import app

runner = CliRunner()

#: Every command group the CLI exposes, plus the root.
COMMANDS = [
    [],
    ["init"],
    ["status"],
    ["add"],
    ["remove"],
    ["serve"],
    ["pin"],
    ["completion"],
    ["auth"],
    ["config"],
]

# The identifier spellings, in the forms a renderer could produce. `--mcp_servers`
# is excluded by the alias check below rather than by this pattern, so that the
# flag staying is a deliberate exception and not a hole in the rule.
IDENTIFIER = re.compile(r"\bmcp_servers?\b|\bMcpServers?\b")


_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def rendered(argv: list[str]) -> str:
    """What `mcp-hangar <argv> --help` actually puts on a terminal, as plain text.

    Two things have to come off before matching, and both have bitten this repo
    already. Rich colourizes when it believes it has a terminal, so
    `--servers,--mcp_servers` arrives shot through with escape sequences and a
    plain `in` check misses it (the same trap `test_init_deps.plain` documents,
    firing the other way round: colour on CI, none here). And Rich wraps at the
    terminal width, so a phrase can be split across two lines.
    """
    result = runner.invoke(app, [*argv, "--help"])
    assert result.exit_code == 0, result.output
    return " ".join(_ANSI.sub("", result.output).split())


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda argv: " ".join(argv) or "root")
def test_help_text_names_no_internal_identifier(argv):
    text = rendered(argv).replace("--servers,--mcp_servers", "--servers")

    offenders = IDENTIFIER.findall(text)

    assert offenders == [], f"`mcp-hangar {' '.join(argv)} --help` shows {set(offenders)}: {text}"


def test_the_root_help_says_what_hangar_is():
    # The old line ("Production-grade MCP mcp_server platform") described a
    # category nobody searches for, in the vocabulary of the code.
    text = rendered([])

    assert "policy enforcement plane" in text
    assert "Production-grade" not in text


def test_the_old_flag_spelling_still_works():
    # Renaming a flag people have in scripts is a breaking change; renaming the
    # one they read is not. So the new name leads and the old one still parses.
    text = rendered(["init"])

    assert "--servers" in text
    assert "--mcp_servers" in text
