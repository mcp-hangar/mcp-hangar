"""An MCP server over HTTP whose one tool reports progress as it runs, for #2314.

    python _progress_server.py <port>

Stateless (2026-07-28) and SSE framed, so the upstream has no standing GET
stream and sends ``notifications/progress`` on the body of the POST response,
the only channel it has. ``count_slowly`` reports each of its three steps half
a second apart, then answers; ``count_quickly`` reports them back to back and
answers at once, so the last progress is right before the result.
"""

import asyncio
import sys

import uvicorn
from mcp.server.mcpserver import Context, MCPServer

STEPS = 3
PAUSE_S = 0.5

mcp = MCPServer("progress-provider")


@mcp.tool(name="count_slowly")
async def count_slowly(ctx: Context) -> str:
    """Report three steps of progress, half a second apart, then answer."""
    for step in range(1, STEPS + 1):
        await ctx.report_progress(step, STEPS, f"step {step}")
        await asyncio.sleep(PAUSE_S)
    return "counted"


@mcp.tool(name="count_quickly")
async def count_quickly(ctx: Context) -> str:
    """Report three steps of progress back to back, then answer at once."""
    for step in range(1, STEPS + 1):
        await ctx.report_progress(step, STEPS, f"step {step}")
    return "counted"


if __name__ == "__main__":
    port = int(sys.argv[1])
    app = mcp.streamable_http_app(host="127.0.0.1", stateless_http=True, json_response=False)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
