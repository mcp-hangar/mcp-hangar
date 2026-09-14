"""A stdio upstream for the reload served tests (#1424). Not collected by pytest.

What a front door's process-wide sections act on, one each:

* ``echo`` takes an argument, so a flat call to it owes the SDK's
  ``Mcp-Param-*`` check (``headers.param_validation``);
* ``links`` hands out three ``resource_link`` references that no catalogue
  lists, so the ones the gateway remembers show only in the links union of
  ``resources/list`` (``resource_links.max_per_tenant``);
* ``ui://panels/main`` is listed only when allowlisted (``ui_resources``).
"""

from mcp.server.mcpserver import MCPServer
from mcp_types import ResourceLink

mcp = MCPServer("reload-upstream")


@mcp.tool(name="echo")
def echo(text: str) -> dict:
    """Echo the text back."""
    return {"echoed": text}


@mcp.tool(name="links")
def links() -> list[ResourceLink]:
    """Hand out three references to resources this upstream does not list."""
    return [ResourceLink(name=f"note-{n}", uri=f"note://{n}", type="resource_link") for n in (1, 2, 3)]


@mcp.resource("ui://panels/main", name="main-panel", mime_type="text/html")
def main_panel() -> str:
    return "<html>panel</html>"


@mcp.resource("note://plain", name="plain-note", mime_type="text/plain")
def plain_note() -> str:
    return "plain note"


if __name__ == "__main__":
    mcp.run()
