"""SEP-1763 ``interceptors/list`` HTTP endpoint.

Exposes mcp-hangar as a discoverable interceptor per the SEP-1763
specification (PR #2624). Registered as a custom HTTP route on the
FastMCP server since the MCP SDK does not yet support custom JSON-RPC
method registration for non-standard methods.
"""

from __future__ import annotations

from typing import Any

from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_hangar import __version__


def interceptors_list_response() -> dict[str, Any]:
    """Build the ``interceptors/list`` response payload."""
    return {
        "interceptors": [
            {
                "name": "mcp-hangar",
                "version": __version__,
                "type": "validator",
                "supportedEvents": ["tools/call", "tools/list"],
                "modes": ["audit", "enforce"],
                "trustBoundary": "host",
            },
        ],
    }


async def interceptors_list_handler(request: Request) -> JSONResponse:
    """Handle ``GET /interceptors/list``."""
    return JSONResponse(interceptors_list_response())
