"""Admin endpoints for runtime withdrawal/restore of a tool, prompt or resource (#235, #1141).

Provides:
    POST /admin/tools/{server}/{name}/withdraw   — runtime withdraw (survives reload)
    POST /admin/tools/{server}/{name}/restore    — remove runtime withdrawal

``name`` is a tool name, a prompt name, or -- for ``kind: "resource"`` -- the
resource's UPSTREAM uri (``demo://doc/1``, ``file:///data/x.txt``), the same
form ``withdrawn_resources:`` reads and ``is_governed_allowed`` matches on,
NOT the projected ``hangar://<upstream>/<uri>``. A uri carries slashes, so the
name segment is a ``path`` converter, anchored by the trailing verb.

Auth: requires ``mcp_servers:lifecycle`` via the existing ``_check_permission``
pattern from ``mcp_servers.py``. How far it reaches depends on the grant's scope:

* a **global** grant acts on any tenant, and an absent ``tenant_id`` means every
  tenant;
* a **tenant-scoped** grant acts on its own tenant only. A ``tenant_id`` naming
  another tenant is refused, and an absent one means its own tenant, never
  every tenant. Lifting a withdrawal that covers every tenant is refused too:
  that decision was made for the whole fleet.

Scope: a withdrawal made here is recorded, reaches the rest of the fleet through
``WithdrawalProjection`` and is folded back in at startup by
``bootstrap.withdrawals`` (#1165). It survives a config reload, a peer's
ignorance and a restart -- which is what the ``{"withdrawn": true}`` in the
response has always implied and, until then, did not deliver.
"""

import json

from starlette.requests import Request
from starlette.routing import Route

from ...application.read_models.tool_projection import get_tool_projection_registry
from ...domain.events import ToolRestored, ToolWithdrawn
from ..context import get_context
from .mcp_servers import _check_permission
from .serializers import HangarJSONResponse
from .tenant_scope import confined_tenant, out_of_scope

_KINDS = ("tool", "prompt", "resource")


async def _parse_body(request: Request) -> tuple[str | None, str] | HangarJSONResponse:
    """``(tenant_id, kind)`` from the optional JSON body, or a 400.

    An absent ``kind`` is a tool -- the only thing this endpoint could withdraw
    before #1141, so a caller who never sent one is unchanged. Anything else
    that is not one of the three kinds is refused outright: falling back to
    ``"tool"`` would write the same-named tool's overlay, which is the
    collateral #1137 describes.
    """
    body: dict = {}
    try:
        parsed = await request.json()
        if isinstance(parsed, dict):
            body = parsed
    except (json.JSONDecodeError, ValueError):
        pass
    kind = body.get("kind", "tool")
    if kind not in _KINDS:
        return HangarJSONResponse(
            {"error": "invalid_kind", "detail": f"kind must be one of {', '.join(_KINDS)}; got {kind!r}"},
            status_code=400,
        )
    return body.get("tenant_id") or None, kind


def _within_grant(request: Request, tenant_id: str | None, *, action: str, target: str) -> tuple[str | None, bool]:
    """``(tenant to act on, whether the grant is tenant-scoped)`` for this request.

    A global grant (or auth off) acts where the body says, and no body tenant
    means every tenant. A tenant-scoped grant acts on its own tenant. No body
    tenant means that tenant -- reading it as "every tenant" is the escalation
    this refuses -- and a body naming another tenant is refused outright rather
    than rewritten. A caller who asked for tenant B must not get tenant A's
    tool withdrawn instead.
    """
    confined = confined_tenant(request)
    if confined is None:
        return tenant_id, False
    if tenant_id is not None and tenant_id != confined:
        raise out_of_scope(
            request, action=action, resource=target, reason="tenant-scoped grant; cannot act on another tenant"
        )
    return confined, True


async def withdraw_tool(request: Request) -> HangarJSONResponse:
    """Withdraw a tool, prompt or resource at runtime for a tenant (or globally).

    Path params:
        server: MCP server identifier.
        tool: Tool name, prompt name, or upstream resource uri (see module docstring).

    Request body (optional JSON):
        tenant_id: Tenant to withdraw for. Omit (or ``null``) to withdraw
            globally for ALL tenants -- with a global grant. With a
            tenant-scoped grant, omitting it means the grant's own tenant, and
            naming another tenant is a 403.
        kind: ``"tool"`` (default), ``"prompt"`` or ``"resource"``. Anything
            else is a 400 and nothing is written.

    Returns:
        JSON with {"withdrawn": true, "mcp_server": ..., "tool": ..., "kind": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    parsed = await _parse_body(request)
    if isinstance(parsed, HangarJSONResponse):
        return parsed
    tenant_id, kind = parsed
    tenant_id, _confined = _within_grant(request, tenant_id, action="withdraw", target=f"{kind}:{server}/{tool}")

    # Applied here as well as by the projection the publish below delivers to.
    # Both, deliberately: the projection is what carries this to peers and back
    # across a restart, but it is registered by bootstrap, and an endpoint whose
    # enforcement depends on a subscription being wired is one wiring bug away
    # from doing nothing at all. Applying twice is applying once.
    get_tool_projection_registry().withdraw(server, tool, tenant_id=tenant_id, kind=kind)

    ctx = get_context()
    ctx.event_bus.publish(ToolWithdrawn(tenant_id=tenant_id, mcp_server=server, tool=tool, kind=kind))

    return HangarJSONResponse(
        {"withdrawn": True, "mcp_server": server, "tool": tool, "kind": kind, "tenant_id": tenant_id}
    )


async def restore_tool(request: Request) -> HangarJSONResponse:
    """Restore a runtime-withdrawn tool, prompt or resource for a tenant (or remove the global entry).

    Affects ONLY the runtime overlay; a config-declared withdrawal independently
    persists (effective = config OR runtime).

    Path params:
        server: MCP server identifier.
        tool: Tool name, prompt name, or upstream resource uri (see module docstring).

    Request body (optional JSON):
        tenant_id: Tenant to restore. Omit (or ``null``) to remove the entire
            runtime entry (all-tenants restore) -- with a global grant. With a
            tenant-scoped grant, omitting it means the grant's own tenant,
            naming another tenant is a 403, and so is restoring a tool whose
            runtime withdrawal covers every tenant.
        kind: ``"tool"`` (default), ``"prompt"`` or ``"resource"``. Anything
            else is a 400 and nothing is written.

    Returns:
        JSON with {"restored": true, "mcp_server": ..., "tool": ..., "kind": ..., "tenant_id": ...}.
    """
    _check_permission(request, resource_type="mcp_servers", action="lifecycle")

    server = request.path_params["server"]
    tool = request.path_params["tool"]
    parsed = await _parse_body(request)
    if isinstance(parsed, HangarJSONResponse):
        return parsed
    tenant_id, kind = parsed
    target = f"{kind}:{server}/{tool}"
    tenant_id, confined = _within_grant(request, tenant_id, action="restore", target=target)

    registry = get_tool_projection_registry()
    # A per-tenant restore leaves an all-tenants entry in place, so this is not
    # about the registry being fooled. Answering {"restored": true} while the
    # tool stays withdrawn would be a lie, and the decision to lift a
    # fleet-wide withdrawal belongs to a fleet-wide grant.
    if confined and registry.is_withdrawn_for_all_tenants(server, tool, kind=kind):
        raise out_of_scope(
            request,
            action="restore",
            resource=target,
            reason="tenant-scoped grant; an all-tenant withdrawal needs a global grant to restore",
        )

    registry.restore(server, tool, tenant_id=tenant_id, kind=kind)

    ctx = get_context()
    ctx.event_bus.publish(ToolRestored(tenant_id=tenant_id, mcp_server=server, tool=tool, kind=kind))

    return HangarJSONResponse(
        {"restored": True, "mcp_server": server, "tool": tool, "kind": kind, "tenant_id": tenant_id}
    )


# Route definitions for mounting in the API router. `{tool:path}` so an
# upstream resource uri (`demo://doc/1`) can ride the segment; the trailing
# verb anchors it. `route_permissions.py` carries the same two templates.
admin_tools_routes = [
    Route("/{server:str}/{tool:path}/withdraw", withdraw_tool, methods=["POST"]),
    Route("/{server:str}/{tool:path}/restore", restore_tool, methods=["POST"]),
]
