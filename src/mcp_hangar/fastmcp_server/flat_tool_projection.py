"""Flat per-tenant tool re-export for front_door topology mode (issue #232).

In front_door mode, external agents see ONLY flat backend tool names (e.g.
``read_item``) instead of the hangar_* meta-API.  This module wires the
per-request-filtered tools/list and flat call dispatch onto the FastMCP
server by re-registering the lowlevel handlers after the default handlers
are set up.

SDK seam used
-------------
FastMCP's ``_setup_handlers()`` (called in ``__init__``) registers
``self.list_tools`` and ``self.call_tool`` on the underlying
``MCPServer._mcp_server`` via the decorators exposed as
``mcp._mcp_server.list_tools()`` and ``mcp._mcp_server.call_tool()``.
These decorators replace ``request_handlers[ListToolsRequest]`` and
``request_handlers[CallToolRequest]`` with new closures and update the
``_tool_cache`` on each list call.  Re-calling those decorators with our own
async functions after construction simply replaces the handlers in the dict,
giving us full per-request control without any private-API subclassing.

See:
  .venv/…/mcp/server/lowlevel/server.py  list_tools() → line 434
                                           call_tool()  → line 492
  .venv/…/mcp/server/fastmcp/server.py   _setup_handlers() → line 302

Collision rule
--------------
When two different backend servers expose a tool with the same flat name,
both tools are SKIPPED and a ``flat_tool_name_collision`` warning is logged.
This is a deliberate security/correctness invariant: exposing an
ambiguously-routed tool could silently send a call to the wrong backend.
Single-backend deployments never hit this path.

Members of ONE group are the exception (#857): they expose the same tool
names by definition -- that is what makes them interchangeable -- so they are
collapsed into their group rather than colliding with each other, and calls
dispatch through the group id so member selection stays with the group's
strategy.

Mode gate
---------
All logic here is active ONLY when the topology mode is ``"front_door"``.
In ``"egress"`` mode the handlers are not replaced and the default hangar_*
surface is fully intact.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any, cast

from mcp.shared.inbound import MCP_PARAM_HEADER_PREFIX, find_invalid_x_mcp_header

from mcp_hangar._sdk_compat import FastMCP, lowlevel_server
from mcp_hangar._sdk_compat import (
    METHOD_NOT_FOUND,
    ListToolsResult,
    Tool as MCPTool,
    is_modern_protocol_version,
    make_mcp_error,
)

from .. import metrics as prometheus_metrics
from ..application.read_models.tool_projection import get_tool_projection_registry
from ..context import get_identity_context
from ..logging_config import should_log_now
from ..domain.services import progress_relay
from ..domain.services.tool_access_resolver import get_tool_access_resolver, PolicyKind
from .resource_link_read_through import project_result_uris

logger = logging.getLogger(__name__)

# --- SEP-2549 cache-scope advertisement for projected lists (issue #292) ------
#
# SEP-2549 defines ``cacheScope`` / ``ttlMs`` as caching hints on list results so
# downstream caches know whether — and for how long — a list response may be
# reused.  ``mcp.types.ListToolsResult`` predates the SEP and has no typed
# top-level ``cacheScope`` / ``ttlMs`` fields (only ``_meta``/``nextCursor``/
# ``tools``), so we advertise the hints under the result's ``_meta`` using the
# SEP-2549 field names.
#
# Cross-tenant isolation is the whole point here.  The hangar fronts MANY tenants
# behind a SINGLE endpoint, and each tenant's ``tools/list`` is a distinct,
# per-request projection.  SEP-2549's bare ``"private"`` enum relies on the
# downstream cache correctly keying by authorization context; if it does not, it
# could serve tenant A's list to tenant B.  To make cross-tenant reuse
# STRUCTURALLY impossible even for a naive cache that keys only on the advertised
# scope, we emit a DISTINCT, stable, opaque scope TOKEN per tenant instead of a
# shared constant.
#
# Fail-closed: when the tenant is unknown (``None``/empty) we emit a unique,
# non-shareable per-request ``no-store`` token so a cache can never get a second
# hit on it — never a shared or global scope.
CACHE_SCOPE_META_KEY = "cacheScope"
CACHE_TTL_META_KEY = "ttlMs"

# Conservative freshness hint (SEP-2549 ``ttlMs`` is in milliseconds).  Small on
# purpose: the projection is cheap to rebuild and changes to a tenant's tool
# surface (withdrawals, policy edits) must propagate quickly.
PROJECTED_LIST_CACHE_TTL_MS = 5_000

# Prefix for real, per-tenant shareable-within-tenant scope tokens.
_TENANT_SCOPE_PREFIX = "tenant"
# Prefix for the fail-closed, non-shareable per-request scope tokens.
_NO_STORE_SCOPE_PREFIX = "no-store"


def derive_tenant_cache_scope(tenant_id: str | None) -> str:
    """Derive a per-tenant SEP-2549 ``cacheScope`` token (pure, unit-testable).

    Properties (relied on by the cross-tenant isolation tests):

    * Two DIFFERENT tenants get DIFFERENT tokens.
    * The SAME tenant gets the SAME token every time (stable).
    * It is NEVER a shared/global constant across tenants.
    * FAIL CLOSED: an unknown tenant (``None`` or empty) yields a unique,
      non-shareable per-request ``no-store`` token that a cache can never reuse,
      and which can never equal a real tenant's token.

    The tenant id is hashed so the raw tenant identifier does not leak into the
    advertised scope; the hash is stable, so the token is stable per tenant.

    Args:
        tenant_id: The calling tenant's id, or ``None``/empty if unknown.

    Returns:
        An opaque, per-tenant (or per-request, when unknown) scope token.
    """
    if not tenant_id:
        # Unknown tenant -> narrowest possible scope.  A fresh uuid guarantees
        # the token is unique to this single response, so any downstream cache
        # keyed on it can never produce a cross-request (or cross-tenant) hit.
        return f"{_NO_STORE_SCOPE_PREFIX}:{uuid.uuid4().hex}"

    digest = hashlib.sha256(tenant_id.encode("utf-8")).hexdigest()[:32]
    return f"{_TENANT_SCOPE_PREFIX}:{digest}"


def build_projected_list_cache_meta(tenant_id: str | None) -> dict[str, Any]:
    """Build the ``_meta`` cache-scope block for a projected list response.

    Attaches the SEP-2549 ``cacheScope`` (per-tenant, fail-closed) and a
    conservative ``ttlMs`` freshness hint.

    Args:
        tenant_id: The calling tenant's id, or ``None`` if unknown.

    Returns:
        A ``_meta`` dict carrying ``cacheScope`` and ``ttlMs``.
    """
    return {
        CACHE_SCOPE_META_KEY: derive_tenant_cache_scope(tenant_id),
        CACHE_TTL_META_KEY: PROJECTED_LIST_CACHE_TTL_MS,
    }


def _member_to_group() -> dict[str, str]:
    """Map each group member's server id to its owning group id.

    Group members are interchangeable by definition, so the flat projection
    must treat them as ONE logical server: policy keys on the group id (the
    same key ``BatchExecutor._gate_tool_access`` uses) and dispatch goes to
    the group so member selection stays with the group's strategy (#857).
    """
    return {member.id: group.id for group in _groups().values() for member in group.members}


def _groups() -> dict[str, Any]:
    """The loaded groups. Imported lazily: `server.bootstrap` imports this module back (#894)."""
    from ..server.bootstrap.composition import GROUPS

    return GROUPS


def _withdrawal_scopes(mcp_server: str) -> tuple[str, ...]:
    """Every id a withdrawal for *mcp_server* can have been declared under (#1037).

    For a group id that is the group AND each of its members, and the union is
    fail-closed: any member's withdrawal hides the item for the whole group.
    Members are interchangeable by definition (#857), so an item withdrawn on
    one of two identical backends is not a state an operator can have meant --
    and the surfaces that ask about a group (prompts, resources) ask under the
    group id alone, so a member's declaration was previously invisible to them.

    For anything else it is the id itself, which is what every caller had.
    """
    group = _groups().get(mcp_server)
    if group is None:
        return (mcp_server,)
    return (mcp_server, *(member.id for member in group.members))


def is_governed_allowed(mcp_server: str, name: str, *, kind: PolicyKind, tenant_id: str | None) -> bool:
    """May *tenant_id* see and use *name* on *mcp_server*? (#1028)

    The single decision behind every projected surface -- tools, prompts and
    resources alike. Both halves of the tool answer, applied per kind:

    * the withdrawal overlay (config or runtime, per tenant or for all), and
    * the effective access policy from the one resolver, with a group member
      checked against its GROUP the way ``_build_flat_map`` has always done it.

    Listing and fetching call this same function, so a thing that was not shown
    cannot be fetched and a thing that was shown can be -- and neither surface
    can drift from the other by growing its own copy of the rule. A denied item
    is answered exactly like a nonexistent one at every call site, which is what
    stops the front door being a cross-tenant enumeration oracle (#905).

    For resources, *name* is the UPSTREAM URI -- see
    :func:`resource_link_read_through._deliverable` for why.
    """
    registry = get_tool_projection_registry()
    for scope in _withdrawal_scopes(mcp_server):
        if registry.is_withdrawn(scope, name, kind=kind, tenant_id=tenant_id):
            if scope != mcp_server:
                logger.debug(
                    "withdrawn_by_group_member scope=%s asked_as=%s kind=%s name=%s", scope, mcp_server, kind, name
                )
            return False
    # Both spellings of one scope resolve to the group: a MEMBER id (how the tool
    # projection is keyed) and the GROUP id itself (what `_upstream_ids` hands the
    # prompts and resources surfaces, having already collapsed the member). Without
    # the second half a group `access:` policy is registered and never read (#1036).
    owner_group = _member_to_group().get(mcp_server) or (mcp_server if mcp_server in _groups() else None)
    return get_tool_access_resolver().is_allowed(
        owner_group or mcp_server,
        name,
        kind=kind,
        group_id=owner_group,
        member_id=tenant_id,
    )


def _build_flat_map(
    tenant_id: str | None,
) -> dict[str, tuple[str, str]]:
    """Build a per-request flat_name -> (mcp_server, tool) map for *tenant_id*.

    Rules applied:
    1. Only tools that are active (not withdrawn) for *tenant_id*.
    2. Only tools the resolver allows for *tenant_id* (member-scope policy).
    3. On flat-name collision across two servers: both entries are dropped and
       a ``flat_tool_name_collision`` warning is emitted.  See module docstring.

    Args:
        tenant_id: The tenant making the request; ``None`` means no identity
            (resolver will deny everything in front_door mode, so the map is
            effectively empty but we still build it correctly).

    Returns:
        Mapping of flat tool name to ``(mcp_server_id, tool_name)``.
    """
    registry = get_tool_projection_registry()
    group_of = _member_to_group()

    flat: dict[str, tuple[str, str]] = {}
    # Track names that collide so we can skip them without re-logging.
    collisions: set[str] = set()

    for raw_proj in registry.all():
        mcp_server = raw_proj.mcp_server
        tool_name = raw_proj.tool

        # Use registry.resolve() to get the overlay-aware projection (runtime +
        # config withdrawals are merged in by the registry, not stored on the raw
        # ToolProjection returned by registry.all()).
        resolved = registry.resolve(mcp_server, tool_name, tenant_id)
        if resolved is None:
            continue

        # Drop withdrawn tools for this tenant (covers both config and runtime overlays).
        if resolved.is_withdrawn_for(tenant_id):
            continue

        # Drop tools denied by policy. A group member is checked against the
        # GROUP policy -- the same check `_gate_tool_access` applies at call
        # time, so a tool shown here is the tool that check will allow. Shared
        # with the prompts and resources surfaces since #1028.
        owner_group = group_of.get(mcp_server)
        if not is_governed_allowed(mcp_server, tool_name, kind="tool", tenant_id=tenant_id):
            continue

        flat_name = tool_name  # FLAT naming: tool name as-is, no server prefix.

        if flat_name in collisions:
            # Already marked as collision; skip silently.
            continue

        if flat_name in flat:
            existing_server, _ = flat[flat_name]
            if group_of.get(existing_server, existing_server) == (owner_group or mcp_server):
                # Same logical server: members of one group expose the same
                # names BY DEFINITION -- that is not ambiguity, keep the first
                # member's entry as the schema source (#857).
                continue
            # Collision across different logical servers: drop the earlier
            # entry too.
            flat.pop(flat_name)
            collisions.add(flat_name)
            logger.warning(
                "flat_tool_name_collision flat_name=%s server_a=%s server_b=%s",
                flat_name,
                existing_server,
                mcp_server,
            )
            continue

        flat[flat_name] = (mcp_server, tool_name)

    return flat


def _build_mcp_tool_list(
    flat_map: dict[str, tuple[str, str]],
) -> list[MCPTool]:
    """Convert the flat map to MCP Tool objects using discovered schemas.

    Args:
        flat_map: Mapping from flat name to (mcp_server, tool_name).

    Returns:
        List of MCP Tool objects ready for the tools/list response.
    """
    registry = get_tool_projection_registry()
    tools: list[MCPTool] = []

    for flat_name, (mcp_server, tool_name) in flat_map.items():
        proj = registry.resolve(mcp_server, tool_name)
        if proj is None:
            continue  # Should not happen after _build_flat_map, but be safe.

        # Carry the WHOLE definition, renaming only what the flat surface owns.
        # Hand-picking three keys dropped `title`, `annotations`, `execution`,
        # `icons`, `_meta` -- and `outputSchema` with them, so a client behind the
        # front door had nothing to validate structured output against (#880).
        # `annotations.readOnlyHint` / `destructiveHint` are what a client uses to
        # decide whether a call needs a human in front of it; a projection that
        # discards them makes every tool look alike.
        payload = dict(proj.schema)
        payload["name"] = flat_name
        payload.setdefault("description", "")
        payload.setdefault("inputSchema", {"type": "object", "properties": {}})

        tools.append(
            # Built via model_validate with the wire alias ``inputSchema`` so the
            # same call works on SDK v1 (field ``inputSchema``) and v2 (renamed to
            # ``input_schema``, alias-populated).
            MCPTool.model_validate(payload)
        )

    return tools


#: Causes an empty front-door projection can have. They are indistinguishable
#: from outside -- same 200, same `{"tools": []}` -- and only one of them is a
#: correct answer.
EMPTY_NO_IDENTITY = "no_identity"
EMPTY_NOTHING_DISCOVERED = "nothing_discovered"
EMPTY_FILTERED = "filtered"

# Per-HTTP-request memo of the identity-scoped projection. The SDK's modern
# transport re-invokes tools/list pre-dispatch to resolve Mcp-Param schemas
# (#1049); without this, that listing rebuilds the map and recounts the metric.
_MEMO_ATTR = "hangar_projection"
_ENVELOPE_ATTR = "hangar_envelope_method"


def _http_request(mcp_ctx: Any) -> Any:
    inner = getattr(mcp_ctx, "request_context", None) or mcp_ctx
    return getattr(inner, "request", None)


def _envelope_method(mcp_ctx: Any) -> str | None:
    """JSON-RPC method of the HTTP request, not of the nested handler.

    A pre-dispatch tools/list runs with ctx.method == "tools/list" even when
    the POST was tools/call. The body (already cached on the Starlette Request
    as ``_body``) is the one source that names the envelope.
    """
    # ponytail: envelope from cached request._body (Starlette fills it after
    # handle_modern_request.body()). Upgrade: the transport passes the method.
    req = _http_request(mcp_ctx)
    if req is None:
        return None
    state = getattr(req, "state", None)
    cached = getattr(state, _ENVELOPE_ATTR, None)
    if isinstance(cached, str):
        return cached
    body = getattr(req, "_body", None)
    if not isinstance(body, (bytes, bytearray)):
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    method = payload.get("method") if isinstance(payload, dict) else None
    if isinstance(method, str) and state is not None:
        try:
            setattr(state, _ENVELOPE_ATTR, method)
        except Exception:  # noqa: BLE001 -- state bags vary; a missed cache is not a miss of the listing
            pass
    return method if isinstance(method, str) else None


def _client_visible_listing(mcp_ctx: Any) -> bool:
    """True when this listing is what a client received, not a schema lookup."""
    return _envelope_method(mcp_ctx) != "tools/call"


_MCP_PARAM_PREFIX_LOWER = MCP_PARAM_HEADER_PREFIX.lower()


def _envelope_payload(mcp_ctx: Any) -> dict[str, Any] | None:
    req = _http_request(mcp_ctx)
    body = getattr(req, "_body", None) if req is not None else None
    if not isinstance(body, (bytes, bytearray)):
        return None
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _called_tool_name(mcp_ctx: Any) -> str | None:
    params = (_envelope_payload(mcp_ctx) or {}).get("params")
    name = params.get("name") if isinstance(params, dict) else None
    return name if isinstance(name, str) else None


def _header_keys(mcp_ctx: Any) -> list[str]:
    headers = getattr(_http_request(mcp_ctx), "headers", None)
    if headers is None:
        return []
    return [str(key) for key in headers]


def _call_carries_param_check(mcp_ctx: Any) -> bool:
    """The SDK only resolves a schema when arguments or Mcp-Param-* headers exist."""
    params = (_envelope_payload(mcp_ctx) or {}).get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if isinstance(arguments, dict) and arguments:
        return True
    return any(key.lower().startswith(_MCP_PARAM_PREFIX_LOWER) for key in _header_keys(mcp_ctx))


def _input_schema_of(tool: MCPTool) -> Any:
    schema = getattr(tool, "input_schema", None)
    return schema if schema is not None else getattr(tool, "inputSchema", None)


def _observe_param_header_skips(mcp_ctx: Any, governed: list[MCPTool], management: list[MCPTool]) -> None:
    """Count an SDK Mcp-Param skip that this listing made observable (#1053)."""
    if _envelope_method(mcp_ctx) != "tools/call" or not _call_carries_param_check(mcp_ctx):
        return
    name = _called_tool_name(mcp_ctx)
    match = next((tool for tool in (*governed, *management) if tool.name == name), None)
    if match is None:
        prometheus_metrics.PARAM_HEADER_VALIDATION_SKIPPED_TOTAL.inc(reason="tool_not_listed")
        return
    if find_invalid_x_mcp_header(_input_schema_of(match)) is not None:
        prometheus_metrics.PARAM_HEADER_VALIDATION_SKIPPED_TOTAL.inc(reason="invalid_annotation")


def _observe_listing_failed_skip(mcp_ctx: Any) -> None:
    if _envelope_method(mcp_ctx) == "tools/call" and _call_carries_param_check(mcp_ctx):
        prometheus_metrics.PARAM_HEADER_VALIDATION_SKIPPED_TOTAL.inc(reason="listing_failed")


def _observe_legacy_param_skip(mcp_ctx: Any) -> None:
    """Handshake-era traffic never runs the Mcp-Param ladder (#1053)."""
    headers = getattr(_http_request(mcp_ctx), "headers", None)
    if headers is None or not hasattr(headers, "get"):
        return
    if not any(key.lower().startswith(_MCP_PARAM_PREFIX_LOWER) for key in _header_keys(mcp_ctx)):
        return
    if is_modern_protocol_version(headers.get("mcp-protocol-version")):
        return
    prometheus_metrics.PARAM_HEADER_VALIDATION_SKIPPED_TOTAL.inc(reason="legacy_protocol")


async def _list_projected_tools(mcp_ctx: Any, load_management: Any) -> ListToolsResult:
    identity = get_identity_context()
    tenant_id: str | None = identity.caller.tenant_id if identity is not None else None
    try:
        cached = _cached_projection(_http_request(mcp_ctx), tenant_id)
        if cached is not None:
            return cached[1]
        flat_map = _build_flat_map(tenant_id)
        return _finish_list_result(
            mcp_ctx,
            tenant_id,
            flat_map,
            _build_mcp_tool_list(flat_map),
            await load_management(mcp_ctx),
        )
    except Exception:  # noqa: BLE001 -- the SDK fail-opens on any listing error; we count then re-raise
        _observe_listing_failed_skip(mcp_ctx)
        raise


def _cached_projection(req: Any, tenant_id: str | None) -> tuple[dict[str, tuple[str, str]], ListToolsResult] | None:
    if req is None:
        return None
    memo = getattr(getattr(req, "state", None), _MEMO_ATTR, None)
    if not isinstance(memo, tuple) or len(memo) != 3:
        return None
    memo_tenant, flat_map, list_result = memo
    if memo_tenant != tenant_id:
        return None
    return flat_map, cast(ListToolsResult, list_result)


def _store_projection(
    req: Any,
    tenant_id: str | None,
    flat_map: dict[str, tuple[str, str]],
    list_result: Any,
) -> None:
    state = getattr(req, "state", None) if req is not None else None
    if state is None:
        return
    try:
        setattr(state, _MEMO_ATTR, (tenant_id, flat_map, list_result))
    except Exception:  # noqa: BLE001 -- a missed memo is a slower request, not a wrong one
        pass


def _flat_map_for_request(mcp_ctx: Any, tenant_id: str | None) -> dict[str, tuple[str, str]]:
    cached = _cached_projection(_http_request(mcp_ctx), tenant_id)
    return cached[0] if cached is not None else _build_flat_map(tenant_id)


def _finish_list_result(
    mcp_ctx: Any,
    tenant_id: str | None,
    flat_map: dict[str, tuple[str, str]],
    governed: list[MCPTool],
    management: list[MCPTool],
) -> ListToolsResult:
    # The SDK's pre-dispatch tools/list on a tools/call (#1049) is not a
    # client-visible listing: do not count it as one.
    if _client_visible_listing(mcp_ctx):
        if not governed:
            _report_empty_projection(tenant_id)
        prometheus_metrics.PROJECTED_TOOLS.observe(len(governed), kind="governed")
        prometheus_metrics.PROJECTED_TOOLS.observe(len(management), kind="management")
    else:
        _observe_param_header_skips(mcp_ctx, governed, management)
    result = ListToolsResult(
        tools=governed + management,
        _meta=build_projected_list_cache_meta(tenant_id),
    )
    _store_projection(_http_request(mcp_ctx), tenant_id, flat_map, result)
    return result


def _classify_empty_projection(tenant_id: str | None) -> str:
    """Why did this projection resolve to nothing?

    Ordered by how wrong the answer is. No identity is a fail-closed deny that
    looks exactly like an empty catalogue; nothing discovered is a replica whose
    boot-time warm-up has not finished or did not succeed; filtered is the one
    case where `[]` is the truth.
    """
    if tenant_id is None:
        return EMPTY_NO_IDENTITY
    if not get_tool_projection_registry().all():
        return EMPTY_NOTHING_DISCOVERED
    return EMPTY_FILTERED


def _report_empty_projection(tenant_id: str | None) -> None:
    """Log and count an empty front-door `tools/list` (#887).

    An operator watching a front door that has just been rolled sees healthy
    pods, a 200, and tenants reporting that everything vanished. Nothing
    distinguished "this tenant has no tools" (correct) from "this replica has
    discovered nothing yet" (wrong, and self-inflicted by a restart).

    Throttled per (reason, tenant): the condition holds for every request while
    it lasts, so the first line is the signal.
    """
    reason = _classify_empty_projection(tenant_id)
    prometheus_metrics.EMPTY_PROJECTION_TOTAL.inc(reason=reason)

    if not should_log_now(f"empty_projection:{reason}:{tenant_id}"):
        return

    if reason == EMPTY_NO_IDENTITY:
        logger.warning(
            "empty_projection reason=no_identity -- front_door served zero tools because the caller "
            "carried no tenant identity. Fail-closed deny, not an empty catalogue: check authentication."
        )
    elif reason == EMPTY_NOTHING_DISCOVERED:
        logger.warning(
            "empty_projection reason=nothing_discovered tenant=%s -- this replica has discovered no tools "
            "at all, so the front door is serving an empty list to a valid tenant. Discovery is per-replica; "
            "front_door warms every configured mcp_server at boot (#885), so seeing this after the first few "
            "seconds means the warm-up failed -- look for front_door_warmup_failed.",
            tenant_id,
        )
    else:
        logger.info(
            "empty_projection reason=filtered tenant=%s -- tools are discovered but policy or withdrawal "
            "removed all of them for this tenant. This is a correct answer.",
            tenant_id,
        )


def _register_caller_progress_forwarder(mcp_ctx: Any) -> str | None:
    """Mint and register an upstream progress token for this call, or ``None`` (#883).

    ``None`` when the caller attached no ``progressToken`` or the context has
    no session to deliver on (the SDK v1 path). The forwarder schedules the
    session's ``send_progress_notification`` onto this loop, because upstream
    progress arrives on the GET stream's reader thread (#882). The upstream is
    asked with a MINTED token, not the caller's: caller tokens are opaque and
    can collide across sessions on a shared upstream client.
    """
    caller_meta = getattr(mcp_ctx, "meta", None) or {}
    caller_token = caller_meta.get("progress_token", caller_meta.get("progressToken"))
    session = getattr(mcp_ctx, "session", None)
    if caller_token is None or session is None:
        return None

    upstream_token = progress_relay.mint_token()
    loop = asyncio.get_running_loop()
    request_id = getattr(mcp_ctx, "request_id", None)

    def _forward(progress: float, total: float | None, message: str | None) -> None:
        asyncio.run_coroutine_threadsafe(
            session.send_progress_notification(
                caller_token,
                progress,
                total=total,
                message=message,
                related_request_id=request_id,
            ),
            loop,
        )

    progress_relay.register(upstream_token, _forward)
    return upstream_token


def register_flat_tool_handlers(mcp: FastMCP) -> None:
    """Replace the default tools/list and tools/call handlers with flat-projection ones.

    This function is called ONLY in front_door mode.  It re-registers the
    request handlers on ``mcp._mcp_server`` (the underlying lowlevel
    ``MCPServer``), overwriting what ``_setup_handlers()`` set up during
    ``FastMCP.__init__``.

    The list handler builds a per-request flat map keyed by caller tenant_id
    and populates ``_tool_cache``.  The call handler resolves the flat name
    from the per-request flat map and routes through the existing
    enforcement+invoke path (resolver + projection + command_bus) without
    duplicating any enforcement logic.

    Args:
        mcp: The FastMCP server instance to modify.
    """
    low = lowlevel_server(mcp)

    async def _management_tools(mcp_ctx: Any) -> list[MCPTool]:
        """The `hangar_*` tools this caller is authorized to call, if any (#904).

        Empty for an agent principal and for an unauthenticated one, which is
        every caller a front door served before this. Definitions come from the
        registered surface rather than being rebuilt here, so a projected
        management tool carries the same schema the invoke path validates.
        """
        # Deferred for the cycle described at the call handler below (#894).
        from ..server.tools.tool_permissions import management_tools_for

        permitted = management_tools_for(mcp_ctx)
        if not permitted:
            return []
        registered = mcp.list_tools()
        if hasattr(registered, "__await__"):
            registered = await registered
        return [tool for tool in registered if tool.name in permitted]

    async def _flat_list_tools(mcp_ctx: Any = None) -> ListToolsResult:
        """Per-request filtered tools/list for front_door mode.

        Reads tenant_id from the identity context (bound at request time by
        the identity middleware, see issue #249).  Projects all active backend
        tools visible to this tenant from the ToolProjectionRegistry, applying
        both member-scope policy (resolver.filter_tools) and withdrawal status.

        Since #904 the `hangar_*` surface is no longer absent by construction:
        it is absent for a caller that may not call it, which is every agent
        principal and every unauthenticated one. An operator holding the
        permissions those tools require sees them here, so one gateway can serve
        an agent without a control plane and an operator with one -- the reason
        the mode-wide swap was not enough (ADR-022).

        The response advertises a per-tenant SEP-2549 ``cacheScope`` under
        ``_meta`` (fail-closed to a non-shareable ``no-store`` token when the
        tenant is unknown) so a downstream cache can never serve one tenant's
        list to another (issue #292).
        """
        return await _list_projected_tools(mcp_ctx, _management_tools)

    async def _flat_call_tool(name: str, arguments: dict[str, Any], mcp_ctx: Any = None) -> Any:
        """Flat tool call dispatch for front_door mode.

        Resolution:
        1. Re-build the flat map for this tenant (same filtering as list).
        2. Resolve flat name → (mcp_server, tool).
        3. Route through the EXISTING enforcement path via BatchExecutor so
           that policy checks, withdrawal rejection, and TOCTOU are handled
           identically to the batch path — no enforcement duplication.

        A name that is not an upstream tool may still be a management tool this
        caller is authorized for (#904), in which case it is dispatched to the
        registered `hangar_*` implementation. The check is the same one the
        listing used, so a tool that was shown is callable and one that was not
        is still `-32601`: not-shown and not-callable are the same decision.

        Protocol errors:
        - Unknown flat name (absent from tenant's current list) → McpError
          with code METHOD_NOT_FOUND (-32601).
        - Tool denied/withdrawn between list and call (TOCTOU) → BatchExecutor
          enforcement path returns ToolAccessDeniedError / ToolWithdrawnError,
          which surfaces as a CallToolResult(isError=True).  The backend is
          never invoked.
        """
        from mcp_hangar._sdk_compat import CallToolResult

        # Imported lazily: the batch package reaches `server.bootstrap`, which
        # imports this module back. At module scope that makes this module
        # impossible to import first in a fresh interpreter (#894).
        from ..server.tools.batch import BatchExecutor, CallSpec
        from ..server.tools.tool_permissions import management_tools_for

        identity = get_identity_context()
        tenant_id: str | None = identity.caller.tenant_id if identity is not None else None
        _observe_legacy_param_skip(mcp_ctx)

        # Re-build flat map for this request's tenant (handles TOCTOU at the
        # map level; enforcement below also re-checks independently). Reuse
        # the per-request memo when the SDK already listed for Mcp-Param
        # validation on this same POST (#1049).
        flat_map = _flat_map_for_request(mcp_ctx, tenant_id)

        if name not in flat_map:
            if name in management_tools_for(mcp_ctx):
                # The registered tool, reached through the SDK's own dispatch so
                # it passes `mcp_tool_wrapper` -- which authorizes it a second
                # time (#909). The context is forwarded because that is where the
                # wrapper reads the principal from; without it the tool would be
                # refused as anonymous.
                return await mcp.call_tool(name, arguments or {}, context=mcp_ctx)
            # Unknown flat name → -32601 (method/tool not found).
            raise make_mcp_error(METHOD_NOT_FOUND, f"Tool '{name}' not found")

        mcp_server_id, tool_name = flat_map[name]
        # A group member dispatches through its GROUP so member selection stays
        # with the group's strategy (round-robin, canary, health) -- the
        # executor resolves the group id to a concrete member itself (#857).
        mcp_server_id = _member_to_group().get(mcp_server_id, mcp_server_id)

        # Relay the caller's progressToken (#883): the upstream is asked with a
        # freshly minted token, and progress arriving on the standing GET
        # stream (#882) is translated back onto this caller's session.
        upstream_token = _register_caller_progress_forwarder(mcp_ctx)

        # Delegate to BatchExecutor.  This reuses the full enforcement path:
        #   resolver.is_tool_allowed → withdrawal check → command_bus.send
        # No enforcement logic is duplicated here.  Run in a worker thread: the
        # executor BLOCKS until the upstream answers, and blocking this loop
        # would freeze every other request on the connection -- including the
        # very progress notifications this call asked for.
        call_id = uuid.uuid4().hex[:12]
        executor = BatchExecutor()
        try:
            batch = await asyncio.to_thread(
                executor.execute,
                batch_id=call_id,
                calls=[
                    CallSpec(
                        index=0,
                        call_id=call_id,
                        mcp_server=mcp_server_id,
                        tool=tool_name,
                        arguments=arguments or {},
                        progress_token=upstream_token,
                    )
                ],
                max_concurrency=1,
                global_timeout=30.0,
                fail_fast=False,
            )
        finally:
            progress_relay.unregister(upstream_token)

        result = batch.results[0]
        if not result.success:
            # Surface enforcement failures as tool errors (isError=True),
            # not as unhandled exceptions, so the MCP envelope stays valid.
            return CallToolResult.model_validate(
                {
                    "content": [{"type": "text", "text": result.error or "tool call failed"}],
                    "isError": True,
                }
            )

        # Namespace every resource URI we are about to hand this tenant with its
        # owning upstream, and remember the resource_links, so following one on
        # this gateway resolves and agrees with the catalogue (#889, #1025).
        project_result_uris(tenant_id, mcp_server_id, result.result)

        # Success — return the raw result dict; the lowlevel handler wraps it.
        return result.result if result.result is not None else {}

    # Register the handlers, replacing the defaults. SDK v1's lowlevel Server
    # exposes list_tools()/call_tool() registration decorators; SDK v2 dropped
    # them for add_request_handler(method, params_type, handler) with a
    # (ctx, params) -> HandlerResult signature.
    if hasattr(low, "list_tools"):  # SDK v1
        low.list_tools()(_flat_list_tools)
        low.call_tool(validate_input=False)(_flat_call_tool)
    else:  # SDK v2
        from mcp_types import CallToolRequestParams, PaginatedRequestParams

        from mcp_hangar._sdk_compat import CallToolResult

        from .asgi import bind_caller_identity, release_caller_identity

        # `ctx` is the SDK's per-request context and carries the HTTP request,
        # so the authenticated principal is right here. It used to be dropped:
        # both handlers then read `identity_context_var`, which the ASGI wrapper
        # sets in a different task, found None, and `_compute_effective_policy`
        # took its `member_id is None` deny-all branch -- front-door mode
        # projected zero tools to every authenticated tenant, with the empty
        # list indistinguishable from "no tools configured".
        # `ctx` is also what carries the principal into the management-surface
        # decision (#904), so it is threaded down rather than only used to bind
        # the tenant: `identity_context_var` carries ids and not roles, and the
        # question here is what this caller may call.
        async def _list_v2(ctx: Any, params: Any) -> ListToolsResult:
            token = bind_caller_identity(ctx)
            try:
                return await _flat_list_tools(ctx)
            finally:
                release_caller_identity(token)

        async def _call_v2(ctx: Any, params: Any) -> Any:
            token = bind_caller_identity(ctx)
            try:
                return await _call_v2_inner(params, ctx)
            finally:
                release_caller_identity(token)

        async def _call_v2_inner(params: Any, ctx: Any) -> Any:
            out = await _flat_call_tool(params.name, params.arguments or {}, ctx)
            if isinstance(out, CallToolResult):  # error path already built one
                return out
            # success path returned the raw backend result dict; wrap it.
            return CallToolResult.model_validate(out) if out else CallToolResult(content=[])

        low.add_request_handler("tools/list", PaginatedRequestParams, _list_v2)
        low.add_request_handler("tools/call", CallToolRequestParams, _call_v2)


def maybe_register_flat_tool_handlers(mcp: Any) -> bool:
    """Install the flat tool surface when topology says ``front_door``.

    In ``front_door`` external agents see flat backend tool names instead of the
    ``hangar_*`` meta-API; in ``egress`` (the default) this is a no-op and the
    meta-API is preserved unchanged. Returns whether the handlers were installed.

    Shared by both builders: the gate used to live only in ``MCPServerFactory``,
    which has no production call site, so ``front_door`` configured on the
    shipped ``serve --http`` silently kept serving the meta-API — the mode
    appeared to do nothing (#596).
    """
    from ..domain.services.tool_access_resolver import is_front_door

    if not is_front_door():
        return False

    register_flat_tool_handlers(mcp)
    logger.info("flat_tool_handlers_registered (topology_mode=front_door)")
    return True
