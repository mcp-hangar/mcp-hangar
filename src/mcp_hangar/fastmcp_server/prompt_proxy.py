"""Front-door proxy for an upstream's prompts (#1024, split from #889).

Carries ``prompts/list`` and ``prompts/get`` through the gateway in
``front_door`` mode, the way the flat tool projection carries tools:

* ``prompts/list`` aggregates per tenant across the tenant's OWN projected
  upstreams -- the logical servers that appear in its flat tool map. Another
  tenant's upstreams are never consulted, so another tenant's prompts are
  never served.
* Naming follows the tool convention (``_build_flat_map``): the flat name is
  the bare prompt name, and a name collision across two different logical
  servers drops BOTH entries with a warning -- an ambiguously-routed
  ``prompts/get`` could fetch from the wrong backend. Members of one group
  are one logical server (#857), so they never collide with each other.
* ``prompts/get`` resolves the flat name to the owning upstream and forwards
  via the thin ``relay_request`` transport (no cold start), mirroring
  :mod:`resource_link_read_through`. An unknown name answers a generic
  not-found without leaking whether it exists for someone else.

Ungoverned by design in this MVP: anything from the tenant's own upstreams is
allowed -- the prompt/resource policy seam is #1028. Registration must run
AFTER ``withdraw_unserved_capabilities``: that pass pops the prompt handlers
nothing serves, and registering real handlers afterwards is exactly what
brings the ``prompts`` capability back (#888, "derived, not inverted").
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from mcp_hangar._sdk_compat import INVALID_PARAMS, lowlevel_server, make_mcp_error

logger = logging.getLogger(__name__)


def _upstream_ids(tenant_id: str | None) -> list[str]:
    """The logical upstream ids this tenant's flat tool map projects.

    The tenant's "own upstreams" are exactly the servers its tool projection
    resolves to -- the same policy- and withdrawal-filtered set the flat tool
    surface serves, with group members collapsed to their group (#857).

    ponytail: an upstream with prompts but zero visible tools for this tenant
    is invisible here; give prompts their own projection when that case is real.
    """
    from .flat_tool_projection import _build_flat_map, _member_to_group

    group_of = _member_to_group()
    return list(dict.fromkeys(group_of.get(server, server) for server, _tool in _build_flat_map(tenant_id).values()))


def _relay(mcp_server_id: str, method: str, params: dict[str, Any]) -> dict[str, Any]:
    """Forward a prompt request to the owning upstream (a group via a member)."""
    from ..server.context import get_context

    ctx = get_context()
    server = ctx.get_mcp_server(mcp_server_id)
    if server is None:
        group = ctx.get_group(mcp_server_id)
        server = group.select_member() if group else None
    if server is None:
        return {"error": {"code": INVALID_PARAMS, "message": "upstream unavailable"}}
    return server.relay_request(method, params)


def _build_prompt_map(tenant_id: str | None) -> dict[str, tuple[str, dict[str, Any]]]:
    """Build flat_name -> (owning logical server, prompt definition) for *tenant_id*.

    Aggregated live from the tenant's upstreams; an upstream that fails to
    answer (not live, no prompts capability) contributes nothing rather than
    failing the whole list.

    ponytail: sequential per-request relay to every upstream, no cache; add a
    prompt projection (discovery-time, like tools) if list latency matters.
    """
    flat: dict[str, tuple[str, dict[str, Any]]] = {}
    collisions: set[str] = set()

    for server_id in _upstream_ids(tenant_id):
        try:
            response = _relay(server_id, "prompts/list", {})
        except Exception:  # noqa: BLE001 -- one dead upstream must not empty the list
            logger.debug("prompt_list_relay_failed mcp_server=%s", server_id, exc_info=True)
            continue
        prompts = (response.get("result") or {}).get("prompts")
        if not isinstance(prompts, list):
            continue
        for prompt in prompts:
            if not (isinstance(prompt, dict) and isinstance(prompt.get("name"), str)):
                continue
            name = prompt["name"]
            if name in collisions:
                continue
            if name in flat:
                existing_server, _ = flat[name]
                if existing_server == server_id:
                    continue  # duplicate within one logical server: keep the first
                flat.pop(name)
                collisions.add(name)
                logger.warning(
                    "flat_prompt_name_collision flat_name=%s server_a=%s server_b=%s",
                    name,
                    existing_server,
                    server_id,
                )
                continue
            flat[name] = (server_id, prompt)

    return flat


def maybe_register_prompt_proxy(mcp: Any) -> bool:
    """Install the prompts proxy in ``front_door`` mode on the SDK v2 surface.

    Returns whether the handlers were installed. Must run after
    ``withdraw_unserved_capabilities`` -- see module docstring.
    """
    from ..domain.services.tool_access_resolver import is_front_door

    if not is_front_door():
        return False

    low = lowlevel_server(mcp)
    if hasattr(low, "list_tools"):  # SDK v1 surface: no prompt proxy
        return False

    from mcp_types import (
        GetPromptRequestParams,
        GetPromptResult,
        ListPromptsResult,
        PaginatedRequestParams,
    )

    from ..context import get_identity_context
    from .asgi import bind_caller_identity, release_caller_identity
    from .flat_tool_projection import build_projected_list_cache_meta
    from .resource_link_read_through import project_result_uris

    def _tenant() -> str | None:
        identity = get_identity_context()
        return identity.caller.tenant_id if identity is not None else None

    async def _list(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            tenant_id = _tenant()
            prompt_map = await asyncio.to_thread(_build_prompt_map, tenant_id)
            return ListPromptsResult.model_validate(
                {
                    "prompts": [prompt for _server, prompt in prompt_map.values()],
                    # Per-tenant SEP-2549 cacheScope, same isolation as tools/list.
                    "_meta": build_projected_list_cache_meta(tenant_id),
                }
            )
        finally:
            release_caller_identity(token)

    async def _get(ctx: Any, params: Any) -> Any:
        token = bind_caller_identity(ctx)
        try:
            name = params.name
            tenant_id = _tenant()
            # Rebuilt per request, same TOCTOU stance as the flat tool call:
            # what was shown is fetchable, what was not stays unknown.
            prompt_map = await asyncio.to_thread(_build_prompt_map, tenant_id)
            if name not in prompt_map:
                raise make_mcp_error(INVALID_PARAMS, f"Unknown prompt: {name}")
            server_id, _prompt = prompt_map[name]
            response = await asyncio.to_thread(
                _relay, server_id, "prompts/get", {"name": name, "arguments": params.arguments or {}}
            )
            if "error" in response:
                error = response["error"]
                raise make_mcp_error(error.get("code", INVALID_PARAMS), error.get("message", "prompt error"))
            result = response.get("result") or {}
            # A prompt message may embed a resource_link too: same front-door
            # URI rewrite as a tool result, or the client gets a URI the
            # gateway cannot resolve (#1025).
            project_result_uris(tenant_id, server_id, result)
            return GetPromptResult.model_validate(result)
        finally:
            release_caller_identity(token)

    low.add_request_handler("prompts/list", PaginatedRequestParams, _list)
    low.add_request_handler("prompts/get", GetPromptRequestParams, _get)
    logger.info("prompt_proxy_registered (topology_mode=front_door)")
    return True
