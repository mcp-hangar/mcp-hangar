"""MCP client streams for the live harness -- streamable-HTTP and stdio.

`streamable_http_client` takes no `headers` kwarg: request headers (e.g.
`Authorization` / `X-API-Key`) ride on a pre-built httpx client passed as
`http_client=`, built with `create_mcp_http_client(headers=...)`. This wraps that
pair so the t0/t1/t2 tests open a stream in one line.
"""

from __future__ import annotations

import contextlib

from mcp import StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import create_mcp_http_client, streamable_http_client


@contextlib.asynccontextmanager
async def open_mcp_streams(url: str, headers: dict[str, str]):
    """Yield the `(read, write)` transport streams for `url` with `headers`.

    async with open_mcp_streams(f"{base}/mcp", headers) as (read, write):
        async with ClientSession(read, write) as session:
            ...
    """
    async with streamable_http_client(url, http_client=create_mcp_http_client(headers=headers)) as streams:
        yield streams


@contextlib.asynccontextmanager
async def open_stdio_streams(command: str, args: list[str], env: dict[str, str]):
    """Yield the `(read, write)` streams for a server spawned over stdio.

    `env` is passed explicitly and always: the SDK hands a *scrubbed*
    environment to the process it spawns, so a variable the test set (and that
    the upstream Hangar spawns is meant to read) never arrives unless it is
    forwarded here. A test that forgets it sees the unchanged server and asserts
    a verdict that never happened.
    """
    params = StdioServerParameters(command=command, args=args, env=env)
    async with stdio_client(params) as streams:
        yield streams
