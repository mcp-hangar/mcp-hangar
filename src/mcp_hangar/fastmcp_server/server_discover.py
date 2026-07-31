"""SEP-2575 ``server/discover`` entry point backed by the per-tenant projection.

SEP-2575 (Stateless MCP) is a MERGED spec method: "Servers **MUST** implement
``server/discover``". Its result advertises the server's supported protocol
versions and capabilities. Here it ALSO returns the tenant-scoped tool surface
read from the existing :class:`ToolProjectionRegistry` (#237), so a client can
discover exactly the tools its tenant is allowed to see — the same surface it
would receive from ``tools/list`` — in a single stateless call.

Tenant resolution and isolation are NOT re-implemented here: the tenant_id is
read from the request-scoped identity context (bound by the identity/auth
middleware, see #249) and the surface is built with the SAME helpers that serve
the per-tenant ``tools/list`` projection (:mod:`flat_tool_projection`). This
guarantees the discover surface is byte-for-byte consistent with ``tools/list``
for the same tenant, and that tenant A can never observe tenant B's tools.

Registered as a custom HTTP route on the FastMCP server (both ``GET`` and a
JSON-RPC ``POST``) since the MCP SDK does not yet expose registration for
non-standard JSON-RPC methods.
"""

from __future__ import annotations

from typing import Any

from mcp_hangar._sdk_compat import FastMCP, lowlevel_server
from mcp_hangar._sdk_compat import DEFAULT_NEGOTIATED_VERSION, LATEST_PROTOCOL_VERSION
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_hangar import __version__
from mcp_hangar.context import get_identity_context
from mcp_hangar.logging_config import get_logger

from .config import HANGAR_SERVER_NAME
from .flat_tool_projection import _build_flat_map, _build_mcp_tool_list

logger = get_logger(__name__)

_METHOD = "server/discover"

# Protocol versions this server supports, newest first. The client should pick
# one from this list for subsequent requests (SEP-2575 DiscoverResult).
_SUPPORTED_VERSIONS: tuple[str, ...] = tuple(dict.fromkeys((LATEST_PROTOCOL_VERSION, DEFAULT_NEGOTIATED_VERSION)))


def _caller_tenant_id() -> str | None:
    """Return the tenant_id bound to the current request, or ``None``.

    Mirrors the resolution used by the per-tenant ``tools/list`` projection so
    the two surfaces are scoped identically.
    """
    identity = get_identity_context()
    return identity.caller.tenant_id if identity is not None else None


def tenant_scoped_tools(tenant_id: str | None) -> list[dict[str, Any]]:
    """Return the tenant-scoped FLAT tool surface as serialized MCP Tool dicts.

    Reuses the per-tenant projection read-model (``_build_flat_map`` +
    ``_build_mcp_tool_list``), so withdrawn and policy-denied tools are absent
    and one tenant never sees another tenant's tools. This is what ``tools/list``
    returns in ``front_door`` topology; in ``egress`` topology ``tools/list``
    serves the ``hangar_*`` meta-API instead — see :func:`_advertised_tools`.
    """
    flat_map = _build_flat_map(tenant_id)
    tools = _build_mcp_tool_list(flat_map)
    return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]


def _advertised_tools(mcp: FastMCP | None, tenant_id: str | None) -> list[dict[str, Any]]:
    """Return the tools this caller would actually get from ``tools/list``.

    Which surface that is depends on topology (see
    ``MCPServerFactory._maybe_register_flat_tool_handlers``): ``front_door``
    replaces ``tools/list`` with the flat per-tenant projection, while ``egress``
    — the default — leaves the ``hangar_*`` meta-API in place.

    Reporting the flat projection unconditionally made discovery answer with an
    empty list on every egress gateway until some backend happened to start,
    which is the only surface a stateless client has to learn from (#606). It
    also contradicted this endpoint's own promise that ``tools`` matches the
    caller's ``tools/list``.
    """
    from ..domain.services.tool_access_resolver import get_tool_access_resolver

    try:
        front_door = get_tool_access_resolver().topology_mode == "front_door"
    except Exception:  # noqa: BLE001 -- an unresolvable topology must not fail discovery
        front_door = False

    if front_door or mcp is None:
        return tenant_scoped_tools(tenant_id)

    # Egress: the caller talks to the hangar_* meta-API. Read it from the tool
    # manager rather than the async `list_tools()` so this stays callable from a
    # sync context (and from tests) without spinning an event loop.
    try:
        registered = mcp._tool_manager.list_tools()
    except Exception:  # noqa: BLE001 -- discovery must answer even if the manager is unavailable
        logger.warning("server_discover_tool_listing_failed", exc_info=True)
        return []

    advertised: list[dict[str, Any]] = []
    for tool in registered:
        entry: dict[str, Any] = {
            "name": tool.name,
            "description": getattr(tool, "description", "") or "",
            "inputSchema": getattr(tool, "parameters", None) or {"type": "object"},
        }
        for attr, key in (("title", "title"), ("output_schema", "outputSchema"), ("annotations", "annotations")):
            value = getattr(tool, attr, None)
            if value:
                entry[key] = value if isinstance(value, dict) else value.model_dump(mode="json", exclude_none=True)
        advertised.append(entry)
    return advertised


def _advertised_capabilities(mcp: FastMCP | None) -> dict[str, Any]:
    """Return the server's REAL capabilities, or the minimal honest set.

    Read from the same ``get_capabilities`` the ``initialize`` handshake uses, so
    the two surfaces cannot disagree about what this server supports. They did:
    ``initialize`` advertised tasks + prompts + resources + the SEP-2133
    governance extensions while this endpoint returned a hardcoded
    ``{"tools": {"listChanged": true}}`` (#605).
    """
    if mcp is None:
        return {"tools": {"listChanged": True}}
    try:
        capabilities = lowlevel_server(mcp).get_capabilities()
        dumped = capabilities.model_dump(mode="json", by_alias=True, exclude_none=True)
        return dumped or {"tools": {"listChanged": True}}
    except Exception:  # noqa: BLE001 -- never fail discovery over a capability read
        logger.warning("server_discover_capabilities_failed", exc_info=True)
        return {"tools": {"listChanged": True}}


def server_discover_result(tenant_id: str | None, mcp: FastMCP | None = None) -> dict[str, Any]:
    """Build the SEP-2575 ``DiscoverResult`` payload for *tenant_id*.

    Shape (SEP-2575): ``supportedVersions`` + ``capabilities`` + ``serverInfo``
    (+ optional ``instructions``), plus the caller's tool surface so a stateless
    client can discover what it may call without a separate ``tools/list``.

    ``capabilities`` are read from the live server when *mcp* is supplied, never
    fabricated: a stateless client has no ``initialize`` to learn them from, so a
    hardcoded set silently told modern clients that Tasks, prompts, resources and
    the governance extensions did not exist (#605).
    """
    return {
        "supportedVersions": list(_SUPPORTED_VERSIONS),
        "capabilities": _advertised_capabilities(mcp),
        "serverInfo": {"name": HANGAR_SERVER_NAME, "version": __version__},
        "instructions": (
            "mcp-hangar governs per-tenant access to backend MCP tools. The "
            "`tools` field lists exactly the tools this caller may call; it "
            "matches this caller's tools/list surface."
        ),
        "tools": _advertised_tools(mcp, tenant_id),
    }


async def server_discover_handler(request: Request, mcp: FastMCP | None = None) -> JSONResponse:
    """Handle ``server/discover`` over HTTP.

    Accepts ``GET /server/discover`` (returns the raw ``DiscoverResult``) and
    ``POST /server/discover`` with a JSON-RPC envelope
    ``{jsonrpc, id, method: "server/discover", params}`` (returns a JSON-RPC
    result envelope). The tenant is resolved from the request-scoped identity
    context, so the surface is per-tenant isolated exactly like ``tools/list``.
    """
    tenant_id = _caller_tenant_id()
    result = server_discover_result(tenant_id, mcp)
    logger.debug("server_discover", tenant_id=tenant_id, tool_count=len(result["tools"]))

    if request.method == "GET":
        return JSONResponse(result)

    # POST: JSON-RPC envelope in, JSON-RPC envelope out.
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001 -- malformed body -> JSON-RPC parse error
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}},
            status_code=400,
        )

    if not isinstance(body, dict):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid Request"}},
            status_code=400,
        )

    req_id = body.get("id")
    if body.get("method") not in (None, _METHOD):
        return JSONResponse(
            {"jsonrpc": "2.0", "id": req_id, "error": {"code": -32601, "message": "Method not found"}},
            status_code=404,
        )

    return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": result})


def register_server_discover(mcp: FastMCP) -> None:
    """Register the ``server/discover`` HTTP route on *mcp* (GET + POST).

    The server instance is closed over so the handler can report the REAL
    capabilities and tool surface instead of a fabricated one (#605, #606).
    """

    async def _handler(request: Request) -> JSONResponse:
        return await server_discover_handler(request, mcp)

    mcp.custom_route("/server/discover", methods=["GET", "POST"], name="server_discover")(_handler)
