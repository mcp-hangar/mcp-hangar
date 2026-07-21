"""SDK-version-agnostic streamable-HTTP client streams for the live harness.

SDK v2 renamed the transport factory ``streamablehttp_client`` ->
``streamable_http_client`` and dropped its ``headers`` kwarg: request headers
(e.g. ``Authorization`` / ``X-API-Key``) now ride on a pre-built httpx client
passed as ``http_client=``, built via ``create_mcp_http_client(headers=...)``.
This helper hides that difference so the t0/t1/t2 live tests open MCP streams
the same way on both SDK generations.
"""

from __future__ import annotations

import contextlib

try:  # SDK v2
    from mcp.client.streamable_http import (
        create_mcp_http_client as _mk_http_client,
        streamable_http_client as _factory,
    )

    _V2 = True
except ImportError:  # SDK v1
    from mcp.client.streamable_http import streamablehttp_client as _factory

    _V2 = False


@contextlib.asynccontextmanager
async def open_mcp_streams(url: str, headers: dict[str, str]):
    """Yield the ``(read, write, _)`` transport streams for ``url`` with ``headers``.

    Usage mirrors the old v1 call site::

        async with open_mcp_streams(f"{base}/mcp", headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                ...
    """
    if _V2:
        # v2 yields (read, write); v1 yielded (read, write, get_session_id). Pad to
        # 3 so the shared ``(read, write, _)`` call sites work on both generations.
        async with _factory(url, http_client=_mk_http_client(headers=headers)) as streams:
            read, write = tuple(streams)[:2]
            yield (read, write, None)
    else:
        async with _factory(url, headers=headers) as streams:
            yield streams
