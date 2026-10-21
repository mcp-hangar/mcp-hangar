"""A stdio MCP server with one tool, named by its first argument, for #1770's init tests.

    python named_tool_server.py <tool-name>

Several copies of one server would serve the same tool names, which the front
door withholds as a collision; a name per copy keeps each one's tool visible.
"""

import sys

from mcp.server.mcpserver import MCPServer

mcp = MCPServer("named-tool")


def _echo(text: str) -> str:
    """Echo the text back."""
    return text


mcp.tool(name=sys.argv[1])(_echo)

if __name__ == "__main__":
    mcp.run(transport="stdio")
