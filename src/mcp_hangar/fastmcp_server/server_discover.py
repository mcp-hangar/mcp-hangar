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

from mcp_hangar._sdk_compat import FastMCP
from mcp_hangar._sdk_compat import DEFAULT_NEGOTIATED_VERSION, LATEST_PROTOCOL_VERSION
from starlette.requests import Request
from starlette.responses import JSONResponse

from mcp_hangar import __version__
from mcp_hangar.context import get_identity_context
from mcp_hangar.logging_config import get_logger

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
    """Return the tenant-scoped tool surface as serialized MCP Tool dicts.

    Reuses the per-tenant projection read-model (``_build_flat_map`` +
    ``_build_mcp_tool_list``) so the content is identical to what ``tools/list``
    returns for *tenant_id*: withdrawn tools and policy-denied tools are absent,
    and one tenant never sees another tenant's tools.
    """
    flat_map = _build_flat_map(tenant_id)
    tools = _build_mcp_tool_list(flat_map)
    return [t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in tools]


def server_discover_result(tenant_id: str | None) -> dict[str, Any]:
    """Build the SEP-2575 ``DiscoverResult`` payload for *tenant_id*.

    Shape (SEP-2575): ``supportedVersions`` + ``capabilities`` + ``serverInfo``
    (+ optional ``instructions``). The tenant-scoped projection surface is
    carried in ``tools`` so a stateless client can discover its allowed tools
    without a separate ``tools/list`` round-trip.
    """
    tools = tenant_scoped_tools(tenant_id)
    return {
        "supportedVersions": list(_SUPPORTED_VERSIONS),
        "capabilities": {"tools": {"listChanged": True}},
        "serverInfo": {"name": "mcp-hangar", "version": __version__},
        "instructions": (
            "mcp-hangar governs per-tenant access to backend MCP tools. The "
            "`tools` field lists exactly the tools this tenant may call; it "
            "matches this tenant's tools/list projection."
        ),
        "tools": tools,
    }


async def server_discover_handler(request: Request) -> JSONResponse:
    """Handle ``server/discover`` over HTTP.

    Accepts ``GET /server/discover`` (returns the raw ``DiscoverResult``) and
    ``POST /server/discover`` with a JSON-RPC envelope
    ``{jsonrpc, id, method: "server/discover", params}`` (returns a JSON-RPC
    result envelope). The tenant is resolved from the request-scoped identity
    context, so the surface is per-tenant isolated exactly like ``tools/list``.
    """
    tenant_id = _caller_tenant_id()
    result = server_discover_result(tenant_id)
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
    """Register the ``server/discover`` HTTP route on *mcp* (GET + POST)."""
    mcp.custom_route("/server/discover", methods=["GET", "POST"], name="server_discover")(server_discover_handler)
