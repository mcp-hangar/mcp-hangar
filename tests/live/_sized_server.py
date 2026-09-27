"""A stdio MCP backend whose one tool answers with as many bytes as it is asked for (#1613)."""

try:  # SDK v2: FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("sized-provider")


@mcp.tool(name="sized")
def sized(size: int) -> dict:
    """Return a text of ``size`` bytes."""
    return {"text": "x" * size}


if __name__ == "__main__":
    mcp.run(transport="stdio")
