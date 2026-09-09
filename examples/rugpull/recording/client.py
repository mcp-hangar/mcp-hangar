"""A tiny MCP client, standing in for Claude Code."""
import anyio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
import subprocess

async def main():
    p = StdioServerParameters(command="mcp-hangar", args=["--config", ".demo.run.yaml", "serve"], env=dict(os.environ))
    async with stdio_client(p, errlog=subprocess.DEVNULL) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            res = await s.call_tool("echo", {"text": "hi"})
            txt = "".join(c.text for c in res.content if hasattr(c, "text")).strip()
            print(("DENIED   " if res.is_error else "ALLOWED  ") + txt)

anyio.run(main)
