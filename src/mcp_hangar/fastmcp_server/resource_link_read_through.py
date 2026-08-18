"""Front-door read-through for ``resource_link`` references the gateway handed out (#889).

The visible half of the prompts/resources gap: a tool result faithfully
proxies ``resource_link`` blocks, and following one on the very connection
that produced it answered ``Unknown resource`` -- the gateway handed out a
reference and then refused it. That is the one case where a client is
actively misled rather than under-served.

This module makes exactly those references resolvable, and nothing more:

* Every ``resource_link`` block relayed through the front door is remembered
  as (tenant, uri) -> owning server, capability-style -- a tenant can only
  read what was handed to *it*.
* ``resources/read`` forwards to the owning upstream via the thin
  ``relay_request`` transport (no cold start). ``ui://`` resources go through
  the fail-closed :class:`UiResourceGuard` first -- with no policy wired the
  answer is deny, by design (SEP-1865).
* ``resources/list`` answers with the caller's handed-out links, and
  ``resources/templates/list`` with an empty list, so the advertised
  ``resources`` capability stays honest (#888) rather than half-true.

The full #889 (upstream catalogue listing, prompts, subscriptions,
completions) stays open -- naming and policy for those mirror decisions not
yet made. Registration must run AFTER ``withdraw_unserved_capabilities``:
that pass pops resources handlers nothing serves, and would silently pop
these too (the recurring hidden-wiring shape).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from mcp_hangar._sdk_compat import lowlevel_server, make_mcp_error

from ..domain.services.ui_resource_guard import UiResourceGuard

logger = logging.getLogger(__name__)

#: JSON-RPC error for an unknown/undeliverable resource (MCP spec).
RESOURCE_NOT_FOUND = -32002

#: Bound on remembered links; oldest handed-out reference is evicted first.
#: ponytail: per-replica in-memory map, move to a shared store if links must
#: survive a restart or be readable cross-replica.
_MAX_LINKS = 4096

_links: OrderedDict[tuple[str | None, str], tuple[str, dict[str, Any]]] = OrderedDict()
_lock = threading.Lock()

#: Default guard: empty allowlist and no consent gate, so every ``ui://``
#: resource is denied until an operator wires policies -- fail-closed.
_ui_guard = UiResourceGuard()


def record_resource_links(tenant_id: str | None, mcp_server_id: str, result: Any) -> None:
    """Remember each ``resource_link`` block of a relayed tool result."""
    content = result.get("content") if isinstance(result, dict) else None
    if not isinstance(content, list):
        return
    for block in content:
        if not (isinstance(block, dict) and block.get("type") == "resource_link" and isinstance(block.get("uri"), str)):
            continue
        key = (tenant_id, block["uri"])
        with _lock:
            _links[key] = (mcp_server_id, block)
            _links.move_to_end(key)
            while len(_links) > _MAX_LINKS:
                _links.popitem(last=False)


def _lookup(tenant_id: str | None, uri: str) -> tuple[str, dict[str, Any]] | None:
    with _lock:
        return _links.get((tenant_id, uri))


def _links_for(tenant_id: str | None) -> list[dict[str, Any]]:
    with _lock:
        return [block for (tenant, _uri), (_server, block) in _links.items() if tenant == tenant_id]


def _relay_read(mcp_server_id: str, uri: str) -> dict[str, Any]:
    """Forward ``resources/read`` to the owning upstream (a group via a member)."""
    from ..server.context import get_context

    ctx = get_context()
    server = ctx.get_mcp_server(mcp_server_id)
    if server is None:
        group = ctx.get_group(mcp_server_id)
        server = group.select_member() if group else None
    if server is None:
        return {"error": {"code": RESOURCE_NOT_FOUND, "message": f"Unknown resource: {uri}"}}
    return server.relay_request("resources/read", {"uri": uri})


def maybe_register_resource_read_through(mcp: Any) -> bool:
    """Install the read-through in ``front_door`` mode on the SDK v2 surface.

    Returns whether the handlers were installed. Must run after
    ``withdraw_unserved_capabilities`` -- see module docstring.
    """
    from ..domain.services.tool_access_resolver import is_front_door

    if not is_front_door():
        return False

    low = lowlevel_server(mcp)
    if hasattr(low, "list_tools"):  # SDK v1 surface: no read-through
        return False

    from mcp_types import (
        ListResourcesResult,
        ListResourceTemplatesResult,
        PaginatedRequestParams,
        ReadResourceRequestParams,
        ReadResourceResult,
    )

    from ..context import get_identity_context
    from .asgi import bind_caller_identity, release_caller_identity

    def _tenant() -> str | None:
        identity = get_identity_context()
        return identity.caller.tenant_id if identity is not None else None

    async def _read(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            uri = str(params.uri)
            tenant_id = _tenant()
            entry = _lookup(tenant_id, uri)
            if entry is None:
                raise make_mcp_error(RESOURCE_NOT_FOUND, f"Unknown resource: {uri}")
            mcp_server_id, _block = entry
            decision = await _ui_guard.enforce(uri, tenant_id, mcp_server_id)
            if not decision.allowed:
                raise make_mcp_error(RESOURCE_NOT_FOUND, f"Resource not deliverable: {uri}")
            response = await asyncio.to_thread(_relay_read, mcp_server_id, uri)
            if "error" in response:
                error = response["error"]
                raise make_mcp_error(error.get("code", RESOURCE_NOT_FOUND), error.get("message", "resource error"))
            return ReadResourceResult.model_validate(response.get("result") or {})
        finally:
            release_caller_identity(token)

    async def _list(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            resources = [
                {"uri": block["uri"], "name": block.get("name") or block["uri"], **_optional(block)}
                for block in _links_for(_tenant())
            ]
            return ListResourcesResult.model_validate({"resources": resources})
        finally:
            release_caller_identity(token)

    async def _templates(ctx: Any, params: Any) -> Any:
        return ListResourceTemplatesResult.model_validate({"resourceTemplates": []})

    low.add_request_handler("resources/read", ReadResourceRequestParams, _read)
    low.add_request_handler("resources/list", PaginatedRequestParams, _list)
    low.add_request_handler("resources/templates/list", PaginatedRequestParams, _templates)
    logger.info("resource_link_read_through_registered (topology_mode=front_door)")
    return True


def _optional(block: dict[str, Any]) -> dict[str, Any]:
    """The optional Resource fields a resource_link block may carry."""
    return {key: block[key] for key in ("description", "mimeType", "title", "size") if key in block}
