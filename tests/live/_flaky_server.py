"""A stdio MCP backend whose one tool fails a chosen number of times, for the T3 retry scenarios.

``flaky(key, failures)`` fails its first ``failures`` calls for each ``key`` and
then succeeds, so one test can drive a retry to success (``failures`` below the
attempt budget) and another to exhaustion (``failures`` above it). The count
lives in this process, which the gateway keeps alive between calls.
"""

from collections import Counter

try:  # SDK v2: FastMCP -> MCPServer
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # SDK v1
    from mcp.server.fastmcp import FastMCP

mcp = FastMCP("flaky-provider")
_calls: Counter[str] = Counter()


@mcp.tool(name="flaky")
def flaky(key: str, failures: int) -> dict:
    """Fail the first ``failures`` calls for ``key``, then return how many calls it took."""
    _calls[key] += 1
    if _calls[key] <= failures:
        raise RuntimeError(f"synthetic transient failure {_calls[key]} of {failures}")
    return {"result": _calls[key]}


if __name__ == "__main__":
    mcp.run(transport="stdio")
