"""A stdio MCP backend whose ``lookup`` mirrors its ``region`` argument in ``Mcp-Param-Region``, for #1295.

A header selector in an L7 egress policy only matches a value the SDK checked
against the body (ADR-025), and that needs a tool that declares the header.
``ping`` declares nothing and takes nothing.
"""

from typing import Annotated

from pydantic import Field

try:  # SDK v2: FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("region-provider")


@mcp.tool(name="lookup")
def lookup(region: Annotated[str, Field(json_schema_extra={"x-mcp-header": "Region"})]) -> dict:
    """Answer for ``region``."""
    return {"region": region}


@mcp.tool(name="ping")
def ping() -> dict:
    """Answer."""
    return {"pong": True}


if __name__ == "__main__":
    mcp.run(transport="stdio")
