"""Front-door projection of an upstream's resources (#1021 + #1025, split from #889).

A resource URI does not carry its owning upstream: ``demo://blob/1`` says
nothing about which server it belongs to, and two upstreams can legitimately
serve the same full URI. The tool-side rule (drop both on collision) is wrong
for a catalogue whose job is to be complete, so the projection **namespaces**
instead: every URI the gateway hands out is rewritten to

    ``hangar://<owning upstream id>/<the upstream's own URI>``

and translated back on ``resources/read``. Collisions then cannot happen --
two upstreams serving ``demo://blob/1`` project to two distinct URIs and both
stay in the catalogue.

Unconditional, not only-on-collision (the sub-question #1025 left open): a
URI must not change shape the moment an unrelated upstream appears, the
handed-out-link path and the catalogue path agree by construction rather than
by a special case, and ``resources/read`` can route straight from the URI. The
one thing that would have broken is the SEP-1865 ``ui://`` guard reading a
rewritten scheme -- so it is enforced on the *decoded upstream* URI. Templates
survive too: the rewrite is verbatim, so an RFC 6570 ``{var}`` passes through
and a client's expansion decodes back correctly.

What is served in ``front_door`` mode:

* ``resources/list`` -- the tenant's catalogue, aggregated live across the
  tenant's OWN projected upstreams (the prompts-proxy scoping), unioned with
  the ``resource_link`` references handed to that tenant, which a dynamic
  upstream may never list.
* ``resources/templates/list`` -- the same aggregation for templates.
* ``resources/read`` -- anything in that catalogue, forwarded to the owning
  upstream via the thin ``relay_request`` transport (no cold start). ``ui://``
  resources go through the fail-closed :class:`UiResourceGuard` first -- with
  no policy wired the answer is deny, by design (SEP-1865).

Every URI surface carries the same rewrite, or a client hands back a URI the
gateway cannot resolve: :func:`project_result_uris` is called wherever an
upstream payload crosses the front door (tool results, prompt results, relayed
task results) and ``resources/read`` projects the URIs of the contents it
returns. It also remembers each handed-out ``resource_link`` as
(tenant, projected uri) -> owning server, capability-style: a link handed to a
tenant keeps resolving even if the upstream stops listing it.

Governed since #1028 through :func:`_deliverable`, which every listing, the
handed-out-links union and ``resources/read`` share, so denied means absent AND
unreadable.

Out of scope here: subscriptions (#1027); if they ever need a policy hook it is
:func:`_deliverable`. Registration must run AFTER
``withdraw_unserved_capabilities``: that pass pops resources handlers nothing
serves, and would silently pop these too (the recurring hidden-wiring shape).
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import OrderedDict
from typing import Any

from mcp_hangar._sdk_compat import lowlevel_server, make_mcp_error

from .. import metrics as prometheus_metrics
from ..domain.services.ui_resource_guard import UiResourceGuard, get_ui_resource_guard

logger = logging.getLogger(__name__)

#: JSON-RPC error for an unknown/undeliverable resource (MCP spec).
RESOURCE_NOT_FOUND = -32002

#: Prefix of a gateway-projected resource URI. Everything after it is
#: ``<upstream id>/<the upstream's own URI, verbatim>``; upstream ids never
#: contain ``/``, so the split is unambiguous and no escaping is needed (which
#: is what keeps RFC 6570 template variables intact).
PROJECTED_PREFIX = "hangar://"

#: Bound on remembered links PER TENANT; a tenant's oldest handed-out
#: reference is evicted first, and only by that tenant's own traffic (#1139).
#: ponytail: per-replica in-memory map, move to a shared store if links must
#: survive a restart or be readable cross-replica.
_MAX_LINKS_PER_TENANT = 4096

#: Bound on the number of tenant maps, least recently used evicted first, so
#: an identity-churning caller cannot trade one exhaustion for another by
#: minting tenants. 1024 is a ceiling against churn, not a memory budget: it is
#: far above the tenants one replica serves, and the worst case it admits
#: (1024 x 4096 small dicts) is bounded rather than small.
_MAX_TENANTS = 1024

_links: OrderedDict[str | None, OrderedDict[str, tuple[str, dict[str, Any]]]] = OrderedDict()
_lock = threading.Lock()


#: Default guard: empty allowlist and no consent gate, so every ``ui://``
#: resource is denied until an operator wires policies -- fail-closed.
def _ui_guard() -> UiResourceGuard:
    """The process guard: config fills its policies, bootstrap its consent gate."""
    return get_ui_resource_guard()


def project_uri(mcp_server_id: str, uri: str) -> str:
    """Namespace an upstream URI with the id of the upstream that owns it."""
    return f"{PROJECTED_PREFIX}{mcp_server_id}/{uri}"


def resolve_uri(uri: str) -> tuple[str, str] | None:
    """Split a projected URI back into ``(upstream id, upstream URI)``."""
    if not uri.startswith(PROJECTED_PREFIX):
        return None
    mcp_server_id, _, upstream_uri = uri[len(PROJECTED_PREFIX) :].partition("/")
    if not mcp_server_id or not upstream_uri:
        return None
    return mcp_server_id, upstream_uri


def project_result_uris(tenant_id: str | None, mcp_server_id: str, payload: Any) -> None:
    """Rewrite every resource URI in a relayed upstream *payload*, in place.

    The single hook for "a URI crosses the front door": tool results, prompt
    results and relayed task results all carry content blocks, and a
    ``resource_link`` handed out in upstream form is a URI the gateway cannot
    resolve. Handed-out links are remembered here as well, so the catalogue
    path and the #1021 read-through path agree by construction.

    A no-op outside ``front_door`` -- nothing projects URIs there, so nothing
    may rewrite them either.

    ponytail: walks the whole payload rather than only the ``content`` list, so
    one function covers every result shape; narrow it if a large
    ``structuredContent`` ever shows up in a profile.
    """
    from ..domain.services.tool_access_resolver import is_front_door

    if not is_front_door():
        return
    _walk(tenant_id, mcp_server_id, payload)


def _walk(tenant_id: str | None, mcp_server_id: str, node: Any) -> None:
    if isinstance(node, list):
        for item in node:
            _walk(tenant_id, mcp_server_id, item)
        return
    if not isinstance(node, dict):
        return
    kind = node.get("type")
    if kind == "resource_link" and isinstance(node.get("uri"), str):
        node["uri"] = project_uri(mcp_server_id, node["uri"])
        _remember(tenant_id, mcp_server_id, node)
        return
    if kind == "resource" and isinstance(node.get("resource"), dict):
        embedded = node["resource"]
        if isinstance(embedded.get("uri"), str):
            embedded["uri"] = project_uri(mcp_server_id, embedded["uri"])
        return
    for value in node.values():
        _walk(tenant_id, mcp_server_id, value)


def _remember(tenant_id: str | None, mcp_server_id: str, block: dict[str, Any]) -> None:
    uri = block["uri"]
    with _lock:
        links = _links.get(tenant_id)
        if links is None:
            links = _links[tenant_id] = OrderedDict()
            while len(_links) > _MAX_TENANTS:
                _, dropped = _links.popitem(last=False)
                prometheus_metrics.record_resource_links_evicted("tenant_map_cap", len(dropped))
        else:
            _links.move_to_end(tenant_id)
        links[uri] = (mcp_server_id, block)
        links.move_to_end(uri)
        evicted = 0
        while len(links) > _MAX_LINKS_PER_TENANT:
            links.popitem(last=False)
            evicted += 1
        prometheus_metrics.record_resource_links_evicted("tenant_cap", evicted)


def _lookup(tenant_id: str | None, uri: str) -> tuple[str, dict[str, Any]] | None:
    with _lock:
        links = _links.get(tenant_id)
        if links is None:
            return None
        _links.move_to_end(tenant_id)  # a read is a use for the tenant-map LRU
        return links.get(uri)


def _links_for(tenant_id: str | None) -> list[dict[str, Any]]:
    """This tenant's handed-out links that policy still lets it see (#1028).

    Filtered here and not only in the catalogue: the links union is a second
    way into ``resources/list``, and an unfiltered one would list what
    ``resources/read`` now refuses -- the drift this seam exists to prevent.
    """
    with _lock:
        links = _links.get(tenant_id)
        remembered = list(links.values()) if links else []

    visible: list[dict[str, Any]] = []
    for server, block in remembered:
        resolved = resolve_uri(block["uri"])
        if resolved is not None and _deliverable(tenant_id, server, resolved[1]):
            visible.append(block)
    return visible


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


def _relay_list(mcp_server_id: str, method: str) -> dict[str, Any]:
    """Forward a catalogue listing to an upstream, reusing the prompts transport."""
    from .prompt_proxy import _relay

    return _relay(mcp_server_id, method, {})


def _deliverable(tenant_id: str | None, mcp_server_id: str, upstream_uri: str) -> bool:
    """May this tenant see and read *upstream_uri* on *mcp_server_id*? (#1028)

    Two fail-closed gates, both on the UPSTREAM uri -- the projected
    ``hangar://`` form namespaces the scheme, and neither a policy pattern an
    operator wrote nor the SEP-1865 guard can read a scheme that has been
    rewritten:

    1. The ``ui://`` guard's pure allowlist decision. It is a *case* of this
       surface rather than a mechanism beside it, and it is checked first
       precisely so it cannot be weakened by policy: an un-allowlisted ``ui://``
       resource is denied whatever the resource policy says, and is now absent
       from the catalogue as well as unreadable. Consent, which ``evaluate``
       cannot resolve, still runs at read time via ``enforce``.
    2. The shared ``(mcp_server, kind, name)`` decision -- withdrawal overlay
       plus effective policy.

    Called from the catalogue build, the handed-out-links union and
    ``_resolve_target``, so listing and reading make one decision.
    """
    if not _ui_guard().evaluate(upstream_uri, tenant_id).allowed:
        return False

    from .flat_tool_projection import is_governed_allowed

    return is_governed_allowed(mcp_server_id, upstream_uri, kind="resource", tenant_id=tenant_id)


#: ``(relay method, result key, URI field)`` for the two catalogue listings.
RESOURCES = ("resources/list", "resources", "uri")
TEMPLATES = ("resources/templates/list", "resourceTemplates", "uriTemplate")


def _build_catalog(tenant_id: str | None, listing: tuple[str, str, str]) -> list[dict[str, Any]]:
    """Aggregate one catalogue listing for *tenant_id*, every URI projected.

    Scope is the tenant's own projected upstreams -- the prompts-proxy rule, so
    another tenant's resources are never consulted. An upstream that fails to
    answer (not live, no resources capability) contributes nothing rather than
    failing the whole listing.

    ponytail: sequential per-request relay to every upstream, no cache; give
    resources a discovery-time projection like tools if list latency matters.
    """
    from .prompt_proxy import _upstream_ids

    method, key, field = listing
    entries: list[dict[str, Any]] = []
    for mcp_server_id in _upstream_ids(tenant_id):
        try:
            response = _relay_list(mcp_server_id, method)
        except Exception:  # noqa: BLE001 -- one dead upstream must not empty the catalogue
            logger.debug("resource_list_relay_failed mcp_server=%s method=%s", mcp_server_id, method, exc_info=True)
            continue
        listed = (response.get("result") or {}).get(key)
        if not isinstance(listed, list):
            continue
        entries += [
            {**entry, field: project_uri(mcp_server_id, entry[field])}
            for entry in listed
            if isinstance(entry, dict)
            and isinstance(entry.get(field), str)
            # Denied => absent, so not-shown and not-readable are one decision.
            # A template is matched as the template string it is: a policy that
            # denies `secret://*` denies `secret://{id}` too.
            and _deliverable(tenant_id, mcp_server_id, entry[field])
        ]
    return entries


def _resolve_target(tenant_id: str | None, uri: str) -> tuple[str, str] | None:
    """Resolve a projected URI to ``(upstream id, upstream URI)`` for this tenant.

    Two ways in, both capability-shaped: the URI names an upstream this tenant
    projects, or it is a link that was handed to this tenant (which keeps
    #1021's promise even for an upstream whose tools have since gone).
    """
    resolved = resolve_uri(uri)
    if resolved is None:
        return None
    mcp_server_id, upstream_uri = resolved
    # Governance re-check on the read path (#1028). A link handed out before a
    # deny landed stops resolving, and the TOCTOU window between listing and
    # reading closes -- the same stance `BatchExecutor` takes for tools.
    if not _deliverable(tenant_id, mcp_server_id, upstream_uri):
        return None
    if _lookup(tenant_id, uri) is not None:
        return resolved

    from .prompt_proxy import _upstream_ids

    return resolved if mcp_server_id in _upstream_ids(tenant_id) else None


def maybe_register_resource_read_through(mcp: Any) -> bool:
    """Install the resources projection in ``front_door`` mode on the SDK v2 surface.

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
    from .flat_tool_projection import build_projected_list_cache_meta

    def _tenant() -> str | None:
        identity = get_identity_context()
        return identity.caller.tenant_id if identity is not None else None

    async def _read(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            uri = str(params.uri)
            tenant_id = _tenant()
            target = await asyncio.to_thread(_resolve_target, tenant_id, uri)
            if target is None:
                raise make_mcp_error(RESOURCE_NOT_FOUND, f"Unknown resource: {uri}")
            mcp_server_id, upstream_uri = target
            # The guard reads the UPSTREAM uri: `ui://` is invisible once the
            # scheme has been namespaced, and a guard that cannot see the
            # scheme it guards is not fail-closed (SEP-1865).
            decision = await _ui_guard().enforce(upstream_uri, tenant_id, mcp_server_id)
            if not decision.allowed:
                raise make_mcp_error(RESOURCE_NOT_FOUND, f"Resource not deliverable: {uri}")
            response = await asyncio.to_thread(_relay_read, mcp_server_id, upstream_uri)
            if "error" in response:
                error = response["error"]
                raise make_mcp_error(error.get("code", RESOURCE_NOT_FOUND), error.get("message", "resource error"))
            result = response.get("result") or {}
            for entry in result.get("contents") or []:
                if isinstance(entry, dict) and isinstance(entry.get("uri"), str):
                    entry["uri"] = project_uri(mcp_server_id, entry["uri"])
            return ReadResourceResult.model_validate(result)
        finally:
            release_caller_identity(token)

    async def _list(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            tenant_id = _tenant()
            catalog = await asyncio.to_thread(_build_catalog, tenant_id, RESOURCES)
            listed = {resource["uri"] for resource in catalog}
            # A handed-out link a dynamic upstream never lists still belongs to
            # this tenant's answer -- the catalogue is a superset of #1021.
            resources = catalog + [
                {"uri": block["uri"], "name": block.get("name") or block["uri"], **_optional(block)}
                for block in _links_for(tenant_id)
                if block["uri"] not in listed
            ]
            return ListResourcesResult.model_validate(
                {
                    "resources": resources,
                    # Per-tenant SEP-2549 cacheScope, same isolation as tools/list.
                    "_meta": build_projected_list_cache_meta(tenant_id),
                }
            )
        finally:
            release_caller_identity(token)

    async def _templates(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            tenant_id = _tenant()
            templates = await asyncio.to_thread(_build_catalog, tenant_id, TEMPLATES)
            return ListResourceTemplatesResult.model_validate(
                {"resourceTemplates": templates, "_meta": build_projected_list_cache_meta(tenant_id)}
            )
        finally:
            release_caller_identity(token)

    low.add_request_handler("resources/read", ReadResourceRequestParams, _read)
    low.add_request_handler("resources/list", PaginatedRequestParams, _list)
    low.add_request_handler("resources/templates/list", PaginatedRequestParams, _templates)
    logger.info("resource_projection_registered (topology_mode=front_door)")
    return True


def _optional(block: dict[str, Any]) -> dict[str, Any]:
    """The optional Resource fields a resource_link block may carry."""
    return {key: block[key] for key in ("description", "mimeType", "title", "size") if key in block}
