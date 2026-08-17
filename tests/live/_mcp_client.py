"""Streamable-HTTP client streams for the live harness.

`streamable_http_client` takes no `headers` kwarg: request headers (e.g.
`Authorization` / `X-API-Key`) ride on a pre-built httpx client passed as
`http_client=`, built with `create_mcp_http_client(headers=...)`. This wraps that
pair so the t0/t1/t2 tests open a stream in one line.
"""

from __future__ import annotations

import contextlib

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
