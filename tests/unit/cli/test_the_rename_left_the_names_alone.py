"""The CLI rename spared what is a name rather than prose (#1195 follow-up).

`mcp_server` -> "MCP server" was right for help text and wrong for three things
that happen to be spelled the same way: a parameter name in an `Args:` entry
(covered by `test_docstring_args_name_real_parameters`), a class, and a
configuration key. The last two are recognisable by their backticks -- this repo
writes code spans for identifiers -- so a docstring saying

    Build an unstarted `MCP server` from one entry of the `MCP servers` map.

is naming a class that does not exist and a config section nobody can write.

A reader who follows either one finds nothing, which is the whole cost: it is
not wrong prose, it is a wrong instruction, and it is invisible to every gate
that checks rendered output because none of this is rendered.
"""

from pathlib import Path
import re

CLI = Path(__file__).resolve().parents[3] / "src" / "mcp_hangar" / "server" / "cli"

#: A code span that reads as English. An identifier never contains a space, so
#: this cannot fire on `McpServer`, `mcp_servers` or `mcp-hangar`.
PROSE_IN_A_CODE_SPAN = re.compile(r"`(MCP servers?|MCP server_id)`")


def test_no_code_span_holds_prose_where_an_identifier_belongs():
    offenders = [
        f"{path.relative_to(CLI.parent.parent.parent)}:{lineno}: {line.strip()}"
        for path in sorted(CLI.rglob("*.py"))
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1)
        if PROSE_IN_A_CODE_SPAN.search(line)
    ]

    assert offenders == [], "a code span names an identifier that does not exist:\n" + "\n".join(offenders)
