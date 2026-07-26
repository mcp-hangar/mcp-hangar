"""Shared 2026-07-28 modern-surface wiring (SEP-2575 discover + SEP-2243 front door).

The modern surface has two halves that must be applied together, and identically,
no matter how the MCP server is built:

1. ``register_modern_surface(mcp)`` -- registers the SEP-2575 ``server/discover``
   entry point on the server (build time).
2. ``wrap_front_door_routing(app)`` -- wraps the MCP ASGI app in the SEP-2243
   front-door routing middleware (serve time).

Both used to live only in ``MCPServerFactory``, which has no production call
site, so the shipped ``mcp-hangar serve --http`` path -- which builds the server
in ``server/bootstrap`` and its ASGI app in ``server/lifecycle`` -- got neither:
``GET /server/discover`` returned 404 on the CLI surface regardless of topology
mode, and no header/body consistency was enforced for legacy-era POSTs carrying
``Mcp-Method`` / ``Mcp-Name`` (#560). Same failure shape as the task relay
(#591/#592): wiring reachable only through the unused factory. Both paths now
call these functions, so the modern surface is a property of the server, not of
which builder happened to construct it.

Scope note -- what is NOT here: the modern *invoke* path needs no wiring. The
SDK's session manager era-routes on the ``MCP-Protocol-Version`` header, so a
stateless 2026-07-28 ``tools/call`` POST to ``/mcp`` is already served next to
the legacy ``initialize`` handshake on the same endpoint, and the SDK itself
enforces SEP-2243 header/body agreement for that era (``HEADER_MISMATCH``). The
middleware here covers the legacy era, where the SDK does not.
"""

from __future__ import annotations

from typing import Any

from .._sdk_compat import FastMCP
from ..logging_config import get_logger
from .front_door_routing import FrontDoorRoutingMiddleware

logger = get_logger(__name__)


def register_modern_surface(mcp: FastMCP) -> None:
    """Register the SEP-2575 ``server/discover`` entry point on *mcp* (#290, #560).

    Adds ``GET``/``POST /server/discover``, the stateless per-tenant discovery
    surface the 2.x line depends on. Tenant scoping is inherited from the
    projection read-model -- this only adds a read entry point, it enforces
    nothing new.
    """
    from .server_discover import register_server_discover

    register_server_discover(mcp)
    logger.info("modern_surface_registered", surface="server/discover")


def wrap_front_door_routing(app: Any, *, mcp_path: str = "/mcp") -> Any:
    """Wrap *app* in the SEP-2243 stateless front-door routing middleware.

    Routes on the ``Mcp-Method`` / ``Mcp-Name`` headers instead of session
    affinity, rejecting a header that contradicts the request body. Requests
    without those headers pass through untouched, so pre-SEP-2243 traffic is
    unaffected. Never used for authorization or tenant selection (see
    :mod:`front_door_routing`).
    """
    return FrontDoorRoutingMiddleware(app, mcp_path=mcp_path)


__all__ = ["register_modern_surface", "wrap_front_door_routing"]
