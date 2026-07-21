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

Mode gate
---------
All logic here is active ONLY when the topology mode is ``"front_door"``.
In ``"egress"`` mode the handlers are not replaced and the default hangar_*
surface is fully intact.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from typing import TYPE_CHECKING, Any

from mcp_hangar._sdk_compat import FastMCP
from mcp_hangar._sdk_compat import (
    METHOD_NOT_FOUND,
    ErrorData,
    ListToolsResult,
    McpError,
    Tool as MCPTool,
)

from ..application.read_models.tool_projection import get_tool_projection_registry
from ..context import get_identity_context
from ..domain.services.tool_access_resolver import get_tool_access_resolver
from ..server.tools.batch import BatchExecutor, CallSpec

if TYPE_CHECKING:
    pass

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
    resolver = get_tool_access_resolver()

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

        # Drop tools denied by the member-scope policy.
        if not resolver.is_tool_allowed(
            mcp_server_id=mcp_server,
            tool_name=tool_name,
            member_id=tenant_id,
        ):
            continue

        flat_name = tool_name  # FLAT naming: tool name as-is, no server prefix.

        if flat_name in collisions:
            # Already marked as collision; skip silently.
            continue

        if flat_name in flat:
            # Collision: drop the earlier entry too.
            existing_server, _ = flat.pop(flat_name)
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

        schema = proj.schema
        input_schema = schema.get("inputSchema", {"type": "object", "properties": {}})
        description = schema.get("description", "")

        tools.append(
            MCPTool(
                name=flat_name,
                description=description,
                inputSchema=input_schema,
            )
        )

    return tools


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
    low = mcp._mcp_server  # The MCPServer (lowlevel)

    @low.list_tools()
    async def _flat_list_tools() -> ListToolsResult:
        """Per-request filtered tools/list for front_door mode.

        Reads tenant_id from the identity context (bound at request time by
        the identity middleware, see issue #249).  Projects all active backend
        tools visible to this tenant from the ToolProjectionRegistry, applying
        both member-scope policy (resolver.filter_tools) and withdrawal status.
        hangar_* meta-tools are intentionally absent — external agents must not
        see the control plane surface.

        The response advertises a per-tenant SEP-2549 ``cacheScope`` under
        ``_meta`` (fail-closed to a non-shareable ``no-store`` token when the
        tenant is unknown) so a downstream cache can never serve one tenant's
        list to another (issue #292).
        """
        identity = get_identity_context()
        tenant_id: str | None = identity.caller.tenant_id if identity is not None else None

        flat_map = _build_flat_map(tenant_id)
        tools = _build_mcp_tool_list(flat_map)
        return ListToolsResult(
            tools=tools,
            _meta=build_projected_list_cache_meta(tenant_id),
        )

    @low.call_tool(validate_input=False)
    async def _flat_call_tool(name: str, arguments: dict[str, Any]) -> Any:
        """Flat tool call dispatch for front_door mode.

        Resolution:
        1. Re-build the flat map for this tenant (same filtering as list).
        2. Resolve flat name → (mcp_server, tool).
        3. Route through the EXISTING enforcement path via BatchExecutor so
           that policy checks, withdrawal rejection, and TOCTOU are handled
           identically to the batch path — no enforcement duplication.

        Protocol errors:
        - Unknown flat name (absent from tenant's current list) → McpError
          with code METHOD_NOT_FOUND (-32601).
        - Tool denied/withdrawn between list and call (TOCTOU) → BatchExecutor
          enforcement path returns ToolAccessDeniedError / ToolWithdrawnError,
          which surfaces as a CallToolResult(isError=True).  The backend is
          never invoked.
        """
        from mcp_hangar._sdk_compat import CallToolResult, TextContent

        identity = get_identity_context()
        tenant_id: str | None = identity.caller.tenant_id if identity is not None else None

        # Re-build flat map for this request's tenant (handles TOCTOU at the
        # map level; enforcement below also re-checks independently).
        flat_map = _build_flat_map(tenant_id)

        if name not in flat_map:
            # Unknown flat name → -32601 (method/tool not found).
            raise McpError(
                ErrorData(
                    code=METHOD_NOT_FOUND,
                    message=f"Tool '{name}' not found",
                )
            )

        mcp_server_id, tool_name = flat_map[name]

        # Delegate to BatchExecutor.  This reuses the full enforcement path:
        #   resolver.is_tool_allowed → withdrawal check → command_bus.send
        # No enforcement logic is duplicated here.
        call_id = uuid.uuid4().hex[:12]
        executor = BatchExecutor()
        batch = executor.execute(
            batch_id=call_id,
            calls=[
                CallSpec(
                    index=0,
                    call_id=call_id,
                    mcp_server=mcp_server_id,
                    tool=tool_name,
                    arguments=arguments or {},
                )
            ],
            max_concurrency=1,
            global_timeout=30.0,
            fail_fast=False,
        )

        result = batch.results[0]
        if not result.success:
            # Surface enforcement failures as tool errors (isError=True),
            # not as unhandled exceptions, so the MCP envelope stays valid.
            return CallToolResult(
                content=[TextContent(type="text", text=result.error or "tool call failed")],
                isError=True,
            )

        # Success — return the raw result dict; the lowlevel handler wraps it.
        return result.result if result.result is not None else {}
