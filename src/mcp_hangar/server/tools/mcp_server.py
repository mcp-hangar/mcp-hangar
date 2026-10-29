"""McpServer interaction tools: hangar_tools, hangar_details, hangar_warm.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.

Note: Tool invocation is handled by hangar_call in batch/.
"""

import logging
from collections.abc import Callable
from typing import Any

from mcp_hangar._sdk_compat import FastMCP

from ...application.commands import StartMcpServerCommand
from ...application.mcp.tooling import mcp_tool_wrapper
from ...application.queries import GetMcpServerQuery, GetMcpServerToolsQuery
from ...application.read_models.tool_projection import get_tool_projection_registry
from ...context import get_identity_context
from ...domain.services import get_tool_access_resolver
from ...domain.services.access_decision import decide
from ...domain.value_objects.access import Caller, Target
from ...domain.value_objects.mcp_server import McpServerState
from ...metrics import TOOLS_FILTERED_TOTAL
from ..context import get_context
from ..validation import (
    charged_by_the_command_bus,
    not_rate_limited,
    tool_error_hook,
    tool_error_mapper,
    validate_mcp_server_id_input,
)

logger = logging.getLogger(__name__)


def _caller_tenant_id() -> str | None:
    """Return the calling request's tenant_id, or None when no identity is bound.

    The tenant is the key the tool-access resolver merges a standalone
    server→member policy on. Listing MUST read it so a tool DENIED for this tenant
    is HIDDEN here too -- matching the invoke path (hangar_call → BatchExecutor),
    which keys the same policy on member_id=<caller tenant>. Without it the listing
    fails open: the denied tool is rejected on invoke yet stays visible here.

    Over streamable-HTTP the ASGI auth wrapper's ``identity_context_var`` is not
    propagated into the per-session tool task. Since #576 that is bridged centrally
    in ``mcp_tool_wrapper`` (it injects the request Context and sets
    ``identity_context_var`` from the authenticated principal before the tool body
    runs), so on SDK v2 ``get_identity_context()`` is populated here. The
    ``current_request_context()`` fallback below is the SDK v1 path (v2 removed that
    ambient ``request_ctx``, so it returns None there). Fully fault-barriered: stdio
    / no-request / unauthenticated paths yield None (server-level policy only).
    """
    identity = get_identity_context()
    if identity is not None:
        return identity.caller.tenant_id
    try:
        from mcp_hangar._sdk_compat import current_request_context

        request_context = current_request_context()
        auth_state = getattr(getattr(request_context, "request", None), "state", None)
        principal = getattr(getattr(auth_state, "auth", None), "principal", None)
        if principal is not None:
            from ...fastmcp_server.asgi import _principal_to_identity_context

            return _principal_to_identity_context(principal).caller.tenant_id
    except Exception:  # noqa: BLE001 -- identity bridging must never break tool listing
        return None
    return None


# =============================================================================
# Helper Functions
# =============================================================================


def _owning_groups(server_id: str) -> tuple[str, ...]:
    """The groups that own *server_id*, read where ``hangar_call`` reads them."""
    from .batch.executor import owners_by_member

    return owners_by_member().get(server_id, ())


def _named_directly(mcp_server: str) -> tuple[Target, ...]:
    """A server named directly, typed for `decide()`: itself and every group that owns it (#1784).

    What ``hangar_call`` names when it is handed this id, so a tool a group
    denies on its member is hidden here as the call is refused there.
    """
    return (Target.server(mcp_server, owning_groups=_owning_groups(mcp_server)),)


def _tool_name(tool: Any) -> str:
    return str(tool["name"]) if isinstance(tool, dict) else str(tool.name)


def _allowed(
    tools: list[Any],
    *,
    targets: tuple[Target, ...],
    tenant_id: str | None,
    name_of: Callable[[Any], str] = _tool_name,
) -> list[Any]:
    """The tools of *tools* a call from this caller would be allowed, decided as the call decides (#1784).

    `decide()` is the decision ``hangar_call`` makes: the tool-access policy
    of every scope on the target's path, under the caller's tenant, and the
    withdrawal overlay. A name is listed only when every target allows it,
    since a call may be routed to any of them; no target means a call that
    is refused, so nothing is listed.
    """
    if not targets:
        return []
    caller = Caller(tenant_id=tenant_id or None)
    resolver, registry = get_tool_access_resolver(), get_tool_projection_registry()
    return [
        tool
        for tool in tools
        if all(
            decide(target, name_of(tool), caller, resolver=resolver, withdrawals=registry).allowed for target in targets
        )
    ]


def _note_filtered(mcp_server: str, total: int, visible: int, *, group_id: str | None = None) -> None:
    if visible >= total:
        return
    TOOLS_FILTERED_TOTAL.set(total - visible, mcp_server=group_id or mcp_server)
    if group_id is None:
        logger.debug(
            "tools_filtered_by_policy: mcp_server_id=%s, total=%d, visible=%d, filtered=%d",
            mcp_server,
            total,
            visible,
            total - visible,
        )
    else:
        logger.debug(
            "tools_filtered_by_policy: mcp_server_id=%s, group_id=%s, total=%d, visible=%d, filtered=%d",
            mcp_server,
            group_id,
            total,
            visible,
            total - visible,
        )


def _listed(tools: list[Any], *, named: str, served_by: str | None = None, tenant_id: str | None) -> list[dict]:
    """Each listed tool as its wire dict, with its digest and any pin next to it (#1528).

    Takes the list AFTER the tool-access filter: a tool this caller cannot list
    gets no digest and no pin, because it is never passed here. A server with
    predefined tools lists the schemas its configuration declares, so that is
    what is digested there.

    A tool that drifted from a blocking pin is left out (#1776): the call is
    refused, and listing it would hand the model the drifted description.
    ``hangar_details`` and REST show it to the operator, with the reason. A
    tool under a blocking pin is served with only the fields the pin covers.
    """
    registry = get_tool_projection_registry()
    # A server named directly is held to the pins of the groups that own it, as its call is.
    groups = () if served_by else _owning_groups(named)
    scopes = dict.fromkeys((named, served_by or named, *groups))
    listed = []
    for tool in tools:
        entry = tool.to_dict()
        fields = registry.digest_fields(entry, named=named, served_by=served_by, tenant_id=tenant_id, groups=groups)
        if "withheld" in fields:
            continue
        entry = registry.served_definition(entry, tenant_id, scopes)
        entry.update(fields)
        listed.append(entry)
    return listed


def _withhold_drifted(mcp_server: str, tools: list[dict], tenant_id: str | None) -> list[dict]:
    """Split the drifted tools out of *tools* for ``hangar_details``; return them withheld (#1776).

    *tools* is the list already filtered by tool access, so a caller is told
    about no tool it could not list (#905). A withheld entry carries its name,
    the reason and the digests, never the drifted description. Each tool left
    in *tools* under a blocking pin is cut down to the fields the pin covers.
    """
    registry = get_tool_projection_registry()
    groups = _owning_groups(mcp_server)
    withheld: list[dict] = []
    for index, tool in reversed(list(enumerate(tools))):
        fields = registry.digest_fields(tool, named=mcp_server, tenant_id=tenant_id, groups=groups)
        if "withheld" in fields:
            del tools[index]
            withheld.insert(0, {"name": tool.get("name", ""), "reason": fields.pop("withheld"), **fields})
        else:
            tools[index] = registry.served_definition(tool, tenant_id, (mcp_server, *groups))
    return withheld


def _govern_details(mcp_server: str, result: dict[str, Any]) -> dict[str, Any]:
    """Cut the tools ``hangar_details`` shows down to what this caller may list, as `hangar_tools` does (#1784).

    Each name is decided as a call naming this server is decided: under the
    server and every group that owns it, for the caller's tenant, withdrawals
    included. The drifted ones are then named, after the access filter, so
    only a tool this caller may list is named (#905).
    """
    if "tools" in result and result["tools"]:
        tenant_id = _caller_tenant_id()
        original_count = len(result["tools"])
        result["tools"] = _allowed(result["tools"], targets=_named_directly(mcp_server), tenant_id=tenant_id)
        filtered_count = original_count - len(result["tools"])
        if filtered_count > 0:
            result["tools_policy"]["filtered_count"] = filtered_count
        if withheld := _withhold_drifted(mcp_server, result["tools"], tenant_id):
            result["withheld_tools"] = withheld
    return result


def _get_tools_for_group(mcp_server: str) -> dict[str, Any]:
    """Get tools for a mcp_server group: what a call from this caller would be allowed (#1784).

    Decided on the members a call for the caller's tenant may be routed to
    (`route_candidates`: the pinned or canary member alone, else every member
    in rotation), each name through the `decide()` the call makes, and
    listed only when every one of them allows it. The catalogue is read from
    one of those members, which is not selected and not started: a listing
    advances no strategy and starts nothing (decision 1 on #1763). A group
    with no member in rotation lists nothing, as a call to it is refused
    (decision 2); it used to raise ``no_healthy_members_in_group``.
    """
    ctx = get_context()
    group = ctx.get_group(mcp_server)
    assert group is not None, f"Group {mcp_server} not found"
    tenant_id = _caller_tenant_id()
    candidates = group.route_candidates(tenant_id)
    if not candidates:
        return {"mcp_server": mcp_server, "group": True, "tools": []}

    answering = next((m for m in candidates if m.state_snapshot is not McpServerState.DEAD), None)
    if answering is None:
        # Listing tools is not a start. A call through the group restarts a
        # crashed member, and that call respects its backoff (#1361).
        return {"mcp_server": mcp_server, "group": True, "state": "dead", "tools": []}

    tools = ctx.query_bus.execute(GetMcpServerToolsQuery(mcp_server_id=answering.mcp_server_id))
    targets = tuple(Target.group(mcp_server, member_server=member.mcp_server_id) for member in candidates)
    allowed = _allowed(tools, targets=targets, tenant_id=tenant_id)
    _note_filtered(answering.mcp_server_id, len(tools), len(allowed), group_id=mcp_server)

    return {
        "mcp_server": mcp_server,
        "group": True,
        "tools": _listed(allowed, named=mcp_server, served_by=answering.mcp_server_id, tenant_id=tenant_id),
    }


def _get_tools_for_mcp_server(mcp_server: str) -> dict[str, Any]:
    """Get tools for a single mcp_server."""
    ctx = get_context()
    mcp_server_obj = ctx.get_mcp_server(mcp_server)
    assert mcp_server_obj is not None, f"Server {mcp_server} not found"
    tenant_id = _caller_tenant_id()
    # Decided as `hangar_call` decides a call naming this server: under every group that owns it too (#1784).
    targets = _named_directly(mcp_server)

    # If mcp_server has predefined tools, return them without starting
    if mcp_server_obj.has_tools:
        tools = mcp_server_obj.tools.list_tools()
        filtered_tools = _allowed(tools, targets=targets, tenant_id=tenant_id)
        _note_filtered(mcp_server, len(tools), len(filtered_tools))

        return {
            "mcp_server": mcp_server,
            "state": mcp_server_obj.state.value,
            "predefined": mcp_server_obj.tools_predefined,
            "tools": _listed(filtered_tools, named=mcp_server, tenant_id=tenant_id),
        }

    if mcp_server_obj.state.value == "dead":
        # Listing tools is not a start, and must not revive a dead server inside
        # its backoff (#1361). A deliberate start, or a call after the backoff,
        # lists them again.
        return {"mcp_server": mcp_server, "state": "dead", "predefined": mcp_server_obj.tools_predefined, "tools": []}

    # Start mcp_server and discover tools
    ctx.command_bus.send(StartMcpServerCommand(mcp_server_id=mcp_server))
    query = GetMcpServerToolsQuery(mcp_server_id=mcp_server)
    tools = ctx.query_bus.execute(query)

    filtered_tools = _allowed(tools, targets=targets, tenant_id=tenant_id)
    _note_filtered(mcp_server, len(tools), len(filtered_tools))

    return {
        "mcp_server": mcp_server,
        "state": mcp_server_obj.state.value,
        "predefined": False,
        "tools": _listed(filtered_tools, named=mcp_server, tenant_id=tenant_id),
    }


# =============================================================================
# Tool Registration
# =============================================================================


def register_mcp_server_tools(mcp: FastMCP) -> None:  # noqa: C901 -- baseline CC=16; split before extending
    """Register mcp_server interaction tools with MCP server.

    Registers:
    - hangar_tools: Get tool schemas for a mcp_server
    - hangar_details: Get detailed mcp_server/group info
    - hangar_warm: Pre-start mcp_servers to avoid cold start latency

    Note: Tool invocation has been consolidated into hangar_call (batch.py).
    """

    @mcp.tool(name="hangar_tools")
    @mcp_tool_wrapper(
        tool_name="hangar_tools",
        rate_limit_key=lambda *_a, **_k: "hangar_tools",
        # A listing of a server that does work sends `StartMcpServerCommand`,
        # which the command bus charges. The rest read tools declared in the
        # config, or the catalogue a group member already discovered (#1481).
        check_rate_limit=charged_by_the_command_bus,
        validate=validate_mcp_server_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_tools(mcp_server: str) -> dict:
        """Get tool schemas (JSON Schema) for a mcp_server.

        CHOOSE THIS when: you need tool names and input schemas before calling.
        CHOOSE hangar_details when: you need mcp_server config, health, or runtime info.
        CHOOSE hangar_call when: you already know the tool name and want to invoke it.

        Side effects: May start a cold mcp_server named directly to discover
        its tools. A group's listing reads what a member in rotation already
        discovered; it selects and starts none.

        Args:
            mcp_server: str - McpServer ID or Group ID

        Returns:
            McpServer: {
                mcp_server: str,
                state: str,
                predefined: bool,
                tools: [{name: str, description: str, inputSchema: object,
                         digest: str, pinned_digest?: str}]
            }
            Group: {
                mcp_server: str,
                group: true,
                state?: "dead",
                tools: [{..., digest: str, pinned_digest?: str}]
            }
            A group lists a tool only when every member a call from you may be
            routed to allows it: the member you are pinned to alone, otherwise
            every member in rotation. A group with no member in rotation, or
            whose routable members are all dead, lists no tools.
            digest is the tool's SHA-256 schema fingerprint, as `mcp-hangar pin`
            computes it. pinned_digest is the pin declared for your tenant (or
            for all tenants) on this id, or on the group member that served the
            listing; absent when none is. A tool that drifted from a pin in
            block mode is not listed (hangar_details names it, with the
            reason); under warn or audit it is listed and they differ.
            Error: ValueError with "unknown_mcp_server: <id>"

        Example:
            hangar_tools("math")
            # {"mcp_server": "math", "state": "ready", "predefined": false,
            #  "tools": [{"name": "add", "description": "Add two numbers",
            #             "inputSchema": {"properties": {"a": {"type": "number"}, "b": {"type": "number"}}}}]}

            hangar_tools("llm-group")
            # {"mcp_server": "llm-group", "group": true, "tools": [...]}

            hangar_tools("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        if ctx.group_exists(mcp_server):
            return _get_tools_for_group(mcp_server)

        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        return _get_tools_for_mcp_server(mcp_server)

    @mcp.tool(name="hangar_details")
    @mcp_tool_wrapper(
        tool_name="hangar_details",
        rate_limit_key=lambda mcp_server: f"hangar_details:{mcp_server}",
        check_rate_limit=not_rate_limited,
        validate=validate_mcp_server_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_details(mcp_server: str) -> dict:
        """Get configuration and runtime info for a mcp_server or group.

        CHOOSE THIS when: you need mcp_server config, health history, or group membership.
        CHOOSE hangar_tools when: you need tool schemas for invoking.
        CHOOSE hangar_status when: you need quick overview of all mcp_servers.

        Side effects: None (read-only).

        Args:
            mcp_server: str - McpServer ID or Group ID

        Returns:
            McpServer: {
                mcp_server_id: str,
                state: str,
                mode: str,
                provenance: str,
                alive: bool,
                tools: [{name, description, inputSchema}],
                withheld_tools?: [{name, reason, digest, pinned_digest?}],
                health: {consecutive_failures: int, last_check: str, ...},
                idle_time: float,
                meta: object,
                dead: {reason, since, retry_allowed_at, revived_by} | null,
                tools_policy: object
            }
            Group: {
                group_id: str,
                description: str,
                state: str,
                strategy: str,
                min_healthy: int,
                healthy_count: int,
                members_in_rotation_count: int,
                total_members: int,
                is_available: bool,
                circuit_open: bool,
                members: [{id, state, in_rotation, weight, priority, consecutive_failures}]
            }
            A group is the same dict GET /api/groups/{id} returns; for a
            caller with a tenant, members and every count, state and
            is_available cover only the members its calls may be routed to
            (the pinned or canary member, else every member in rotation;
            none when no member is selectable). healthy_count
            counts members that are ready and in rotation;
            members_in_rotation_count counts members in rotation in any state.
            dead is null unless state is "dead". reason is "given_up",
            "crashed", "start_failed" or "capability_blocked" ("unknown" only
            for a server restored from an older record). since is when it went
            dead; retry_allowed_at is when its backoff ends at the latest, or
            null when no call starts it or no backoff applies (ISO 8601 UTC).
            revived_by is "call_or_start" (hangar_start, or a call after the
            backoff) or "start" (only hangar_start, for capability_blocked).
            Never upstream error text.
            withheld_tools lists the tools left out of tools and hangar_tools
            because they drifted from a pin in block mode (reason
            "digest_drift"), without their description; absent when none is.
            Re-pinning (`mcp-hangar pin --write`) returns them.
            Error: ValueError with "unknown_mcp_server: <id>"

        Example:
            hangar_details("math")
            # {"mcp_server_id": "math", "state": "ready", "mode": "subprocess",
            #  "alive": true, "tools": [...], "health": {"consecutive_failures": 0},
            #  "idle_time": 12.5, "meta": {}}

            hangar_details("llm-group")
            # {"group_id": "llm-group", "state": "healthy", "strategy": "round_robin",
            #  "healthy_count": 2, "total_members": 3, "members": [...]}

            hangar_details("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        if ctx.group_exists(mcp_server):
            group = ctx.get_group(mcp_server)
            assert group is not None
            # A tenant is shown the members its calls may be routed to, as
            # hangar_tools decides on them (#2019); no tenant, the whole group.
            tenant_id = _caller_tenant_id()
            return group.to_status_dict() if tenant_id is None else group.status_for_tenant(tenant_id)

        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        query = GetMcpServerQuery(mcp_server_id=mcp_server)
        result = ctx.query_bus.execute(query).to_dict()
        assert isinstance(result, dict)

        # Add tool access policy summary
        resolver = get_tool_access_resolver()
        result["tools_policy"] = resolver.get_policy_summary(mcp_server)

        return _govern_details(mcp_server, result)

    @mcp.tool(name="hangar_warm")
    @mcp_tool_wrapper(
        tool_name="hangar_warm",
        rate_limit_key=lambda *_a, **_k: "hangar_warm",
        # Each start it sends is charged by the command bus (#1481).
        check_rate_limit=charged_by_the_command_bus,
        validate=None,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def hangar_warm(mcp_servers: str | None = None) -> dict:
        """Pre-start mcp_servers to avoid cold start latency on first hangar_call.

        CHOOSE THIS when: warming multiple mcp_servers before latency-sensitive batch.
        CHOOSE hangar_start when: starting a specific mcp_server or group.
        CHOOSE hangar_call when: invoking tools (auto-starts, latency acceptable).
        SKIP THIS for normal use - hangar_call auto-starts mcp_servers.

        Side effects: Starts specified mcp_server processes. Groups are skipped.
        Warming all skips dead mcp_servers; name one to start it.

        Args:
            mcp_servers: str - Comma-separated mcp_server IDs, or null to warm all

        Returns:
            {
                warmed: list[str],
                already_warm: list[str],
                skipped_dead: list[str],
                failed: list[{id: str, error: str}],
                summary: str
            }

        Example:
            hangar_warm("math,sqlite")
            # {"warmed": ["math"], "already_warm": ["sqlite"], "failed": [],
            #  "summary": "Warmed 1 mcp_servers, 1 already warm, 0 failed"}

            hangar_warm("unknown,math")
            # {"warmed": ["math"], "already_warm": [],
            #  "failed": [{"id": "unknown", "error": "McpServer not found"}],
            #  "summary": "Warmed 1 mcp_servers, 0 already warm, 1 failed"}

            hangar_warm()
            # {"warmed": ["math", "sqlite"], "already_warm": [], "failed": [],
            #  "summary": "Warmed 2 mcp_servers, 0 already warm, 0 failed"}
        """
        ctx = get_context()

        # Parse mcp_server list
        if mcp_servers:
            mcp_server_ids = [p.strip() for p in mcp_servers.split(",") if p.strip()]
        else:
            mcp_server_ids = list(ctx.repository.get_all().keys())

        warmed = []
        already_warm = []
        skipped_dead = []
        failed = []

        for mcp_server_id in mcp_server_ids:
            # Skip groups
            if ctx.group_exists(mcp_server_id):
                continue

            if not ctx.mcp_server_exists(mcp_server_id):
                failed.append({"id": mcp_server_id, "error": "McpServer not found"})
                continue

            try:
                mcp_server_obj = ctx.get_mcp_server(mcp_server_id)
                state = mcp_server_obj.state.value if mcp_server_obj else None
                if state == "ready":
                    already_warm.append(mcp_server_id)
                elif state == "dead" and not mcp_servers:
                    # Warming everything is not a deliberate start of each
                    # server, and only a deliberate start revives a dead one (#1361).
                    skipped_dead.append(mcp_server_id)
                else:
                    command = StartMcpServerCommand(mcp_server_id=mcp_server_id)
                    ctx.command_bus.send(command)
                    warmed.append(mcp_server_id)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: single mcp_server warm failure must not crash batch
                failed.append({"id": mcp_server_id, "error": str(e)[:100]})

        summary = f"Warmed {len(warmed)} mcp_servers, {len(already_warm)} already warm, {len(failed)} failed"
        if skipped_dead:
            summary += f", {len(skipped_dead)} dead skipped"
        return {
            "warmed": warmed,
            "already_warm": already_warm,
            "skipped_dead": skipped_dead,
            "failed": failed,
            "summary": summary,
        }
