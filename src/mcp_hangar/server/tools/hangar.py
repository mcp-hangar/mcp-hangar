"""Control plane management tools: list, start, stop, status, load, unload.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import unicodedata

from mcp_hangar._sdk_compat import FastMCP

from ...application.commands import (
    LoadMcpServerCommand,
    ReloadConfigurationCommand,
    StartMcpServerCommand,
    StopMcpServerCommand,
    UnloadMcpServerCommand,
)
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...application.queries import ListMcpServersQuery
from ...domain.exceptions import (
    MissingSecretsError,
    McpServerNotHotLoadedError,
    RegistryAmbiguousSearchError,
    RegistryServerNotFoundError,
    UnverifiedMcpServerError,
)
from ...domain.value_objects import GroupState, McpServerState
from ..context import get_context
from ..validation import (
    check_rate_limit,
    not_rate_limited,
    tool_error_hook,
    tool_error_mapper,
    validate_mcp_server_id_input,
)
from .replica_view import observe_replica

#: The dashboard's version of `scope_note`, shorter because it sits above a
#: frame rather than in a field an operator reads on its own.
_DASHBOARD_SCOPE_LINE = "This replica's own view, not the fleet. Other replicas can differ."


def hangar_list(state_filter: str | None = None) -> dict:
    """
    List all managed mcp_servers and groups with lifecycle state and metadata.

    This is a QUERY operation - no side effects, only reads data.

    Args:
        state_filter: Optional filter by state (cold, ready, degraded, dead)

    Returns:
        Dictionary with 'mcp_servers', 'groups', and 'runtime_mcp_servers' keys
    """
    from ..state import get_runtime_mcp_servers

    ctx = get_context()

    # Query via CQRS query bus
    query = ListMcpServersQuery(state_filter=state_filter)
    summaries = ctx.query_bus.execute(query)

    # Read groups from context
    groups_list = []
    for group_id, group in ctx.groups.items():
        group_info = group.to_status_dict()
        if state_filter and group_info.get("state") != state_filter:
            continue
        groups_list.append(group_info)

    # Read runtime (hot-loaded) mcp_servers
    runtime_store = get_runtime_mcp_servers()
    runtime_mcp_servers_list = []
    for mcp_server, metadata in runtime_store.list_all():
        mcp_server_state = mcp_server.state.value if hasattr(mcp_server, "state") else "unknown"
        if state_filter and mcp_server_state != state_filter:
            continue
        runtime_mcp_servers_list.append(
            {
                "mcp_server": str(mcp_server.mcp_server_id),
                "state": mcp_server_state,
                "source": metadata.source,
                "verified": metadata.verified,
                "ephemeral": metadata.ephemeral,
                "loaded_at": metadata.loaded_at.isoformat(),
                "lifetime_seconds": round(metadata.lifetime_seconds(), 1),
            }
        )

    return {
        "mcp_servers": [s.to_dict() for s in summaries],
        "groups": groups_list,
        "runtime_mcp_servers": runtime_mcp_servers_list,
    }


def register_hangar_tools(mcp: FastMCP) -> None:  # noqa: C901 -- baseline CC=18; split before extending
    """Register control plane management tools with MCP server."""

    @mcp.tool(name="hangar_list")
    @mcp_tool_wrapper(
        tool_name="hangar_list",
        rate_limit_key=key_global,
        check_rate_limit=not_rate_limited,
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _hangar_list(state_filter: str | None = None) -> dict:
        """List all mcp_servers and groups with precise numeric values.

        CHOOSE THIS when: you need exact data for processing, filtering, or automation.
        CHOOSE hangar_status when: you need human-readable dashboard with visual indicators.
        CHOOSE hangar_group_list when: you need member-level details (rotation, weights).

        Side effects: None (read-only).

        Args:
            state_filter: str - Filter by state: "cold", "ready", "degraded", "dead" (default: null)

        Returns:
            {
                mcp_servers: [{
                    mcp_server: str,
                    state: str,
                    mode: str,
                    alive: bool,
                    tools_count: int,
                    health_status: str,
                    tools_predefined: bool,
                    description?: str
                }],
                groups: [{group_id, state, strategy, healthy_count, total_members, ...}],
                runtime_mcp_servers: [{
                    mcp_server: str,
                    state: str,
                    source: str,
                    verified: bool,
                    ephemeral: bool,
                    loaded_at: str,
                    lifetime_seconds: float
                }]
            }

        Example:
            hangar_list()
            # {"mcp_servers": [{"mcp_server": "math", "state": "ready", "mode": "subprocess",
            #   "alive": true, "tools_count": 2, "health_status": "healthy"}],
            #  "groups": [], "runtime_mcp_servers": []}

            hangar_list(state_filter="ready")
            # Returns only mcp_servers/groups in "ready" state

            hangar_list(state_filter="cold")
            # {"mcp_servers": [{"mcp_server": "sqlite", "state": "cold", "alive": false}], ...}
        """
        return hangar_list(state_filter)

    @mcp.tool(name="hangar_start")
    @mcp_tool_wrapper(
        tool_name="hangar_start",
        rate_limit_key=lambda mcp_server: f"hangar_start:{mcp_server}",
        check_rate_limit=check_rate_limit,
        validate=validate_mcp_server_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_start(mcp_server: str) -> dict:
        """Start a mcp_server or all members of a group.

        CHOOSE THIS when: you need to verify startup works or pre-warm a specific mcp_server/group.
        CHOOSE hangar_warm when: you need to pre-warm multiple mcp_servers at once.
        CHOOSE hangar_call when: you want to invoke a tool (auto-starts cold mcp_servers).
        SKIP THIS when: you just want to call a tool - hangar_call auto-starts mcp_servers.

        Side effects: Starts mcp_server process/container. State changes from cold to ready.

        Args:
            mcp_server: str - McpServer ID or Group ID

        Returns:
            McpServer: {mcp_server: str, state: str, tools: list[str]}
            Group: {
                group: str,
                state: str,
                members_started: int,
                healthy_count: int,
                members_in_rotation_count: int,
                total_members: int
            }
            Error: ValueError with "unknown_mcp_server: <id>" or "unknown_group: <id>"

        Example:
            hangar_start("math")
            # {"mcp_server": "math", "state": "ready", "tools": ["add", "multiply"]}

            hangar_start("llm-group")
            # {"group": "llm-group", "state": "ready", "members_started": 2,
            #  "healthy_count": 2, "members_in_rotation_count": 2, "total_members": 3}

            hangar_start("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        # Check if it's a group first
        if ctx.group_exists(mcp_server):
            group = ctx.get_group(mcp_server)
            assert group is not None
            started = group.start_all()
            return {
                "group": mcp_server,
                "state": group.state.value,
                "members_started": started,
                "healthy_count": group.healthy_count,
                "members_in_rotation_count": group.members_in_rotation_count,
                "total_members": group.total_count,
            }

        # Check mcp_server exists
        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        # Send command via CQRS command bus
        command = StartMcpServerCommand(mcp_server_id=mcp_server)
        result = ctx.command_bus.send(command)
        assert isinstance(result, dict)
        return result

    @mcp.tool(name="hangar_stop")
    @mcp_tool_wrapper(
        tool_name="hangar_stop",
        rate_limit_key=lambda mcp_server: f"hangar_stop:{mcp_server}",
        check_rate_limit=check_rate_limit,
        validate=validate_mcp_server_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def hangar_stop(mcp_server: str) -> dict:
        """Stop a mcp_server or all members of a group.

        CHOOSE THIS when: you need to force restart or free resources immediately.
        CHOOSE hangar_unload when: removing a hot-loaded mcp_server permanently.
        SKIP THIS when: "cleaning up" between calls - mcp_servers auto-manage via idle_ttl.

        Side effects: Stops mcp_server process/container. State changes to cold.

        Args:
            mcp_server: str - McpServer ID or Group ID

        Returns:
            McpServer: {stopped: str, reason: str}
            Group: {group: str, state: str, stopped: bool}
            Error: ValueError with "unknown_mcp_server: <id>"

        Example:
            hangar_stop("math")
            # {"stopped": "math", "reason": "manual"}

            hangar_stop("llm-group")
            # {"group": "llm-group", "state": "cold", "stopped": true}

            hangar_stop("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        # Check if it's a group first
        if ctx.group_exists(mcp_server):
            group = ctx.get_group(mcp_server)
            assert group is not None
            group.stop_all()
            return {
                "group": mcp_server,
                "state": group.state.value,
                "stopped": True,
            }

        # Check mcp_server exists
        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        # Send command via CQRS command bus
        command = StopMcpServerCommand(mcp_server_id=mcp_server)
        result = ctx.command_bus.send(command)
        assert isinstance(result, dict)
        return result

    @mcp.tool(name="hangar_status")
    @mcp_tool_wrapper(
        tool_name="hangar_status",
        rate_limit_key=key_global,
        check_rate_limit=not_rate_limited,
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _hangar_status() -> dict:
        """Get a human-readable status dashboard of the replica that answers.

        CHOOSE THIS when: you need to display status to user or quick health overview.
        CHOOSE hangar_list when: you need exact values for processing or filtering.
        CHOOSE hangar_health when: you need system health with security metrics.

        SCOPE: replica-local. With more than one replica, this is what the
        replica named in replica.instance_id knows, not the fleet. Two calls can
        reach two replicas and disagree without anything having changed. Uptime
        is that replica's process uptime. Reads the same snapshot as
        hangar_health, so the two agree when one replica answers both.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            {
                mcp_servers: [{id: str, indicator: str, state: str, mode: str, last_used?: str}],
                groups: [{
                    id: str,
                    indicator: str,
                    state: str,
                    healthy_members: int,
                    members_in_rotation_count: int,
                    total_members: int,
                    circuit_open: bool
                }],
                runtime_mcp_servers: [{id: str, indicator: str, state: str, source: str, verified: bool}],
                summary: {healthy_mcp_servers: int, total_mcp_servers: int, uptime: str, uptime_seconds: float},
                replica: {instance_id: str, uptime_seconds: float, uptime: str},
                scope: "replica",
                scope_note: str,
                formatted: str
            }
            summary.uptime and summary.uptime_seconds are the answering replica's
            uptime, the same values as replica.uptime and replica.uptime_seconds.
            Two vocabularies, kept apart, each in its own section of `formatted`:
            servers (and runtime_mcp_servers) have a lifecycle state, with
            indicators [READY], [COLD], [STARTING] (state "initializing"),
            [DEGRADED], [DEAD]. Groups have an availability state computed from
            their members, with indicators [HEALTHY], [PARTIAL], [INACTIVE],
            [DEGRADED]. A group is never "cold"; its members are.

        Example:
            hangar_status()
            # {"mcp_servers": [{"id": "math", "indicator": "[READY]", "state": "ready", "mode": "subprocess"}],
            #  "groups": [], "runtime_mcp_servers": [],
            #  "summary": {"healthy_mcp_servers": 1, "total_mcp_servers": 1, "uptime": "2h 15m"},
            #  "replica": {"instance_id": "hangar-0-3fa81c2e", "uptime_seconds": 8100.0, "uptime": "2h 15m"},
            #  "scope": "replica", "scope_note": "This describes what the replica named ...",
            #  "formatted": "...ASCII dashboard naming the replica..."}
        """
        return hangar_status()


def hangar_status() -> dict:
    """Status of the servers and groups on the replica that answers.

    Rendered from `observe_replica()`, the snapshot `hangar_health` also reads,
    and scoped to the replica by name: two replicas answer with two different
    `replica.instance_id` values, so their answers cannot be read as two moments
    of one fleet (#1380).
    """
    view = observe_replica()

    mcp_servers_status = []
    for summary in view.configured:
        state = summary.state
        mcp_server_info: dict = {
            "id": summary.mcp_server_id,
            "indicator": _get_status_indicator(state),
            "state": state,
            "mode": summary.mode,
        }
        if state == "cold":
            mcp_server_info["note"] = "Will start on first request"
        elif state == "dead":
            mcp_server_info["note"] = "Failed: hangar_start starts it again"
        mcp_servers_status.append(mcp_server_info)

    groups_status = [
        {
            "id": group.group_id,
            "indicator": _group_indicator(group.state),
            "state": group.state,
            "healthy_members": group.healthy_count,
            "members_in_rotation_count": group.members_in_rotation_count,
            "total_members": group.total_count,
            "circuit_open": group.circuit_open,
        }
        for group in view.groups
    ]

    runtime_status = [
        {
            "id": server.mcp_server_id,
            "indicator": _get_status_indicator(server.state),
            "state": server.state,
            "source": server.metadata.source,
            "verified": server.metadata.verified,
            "hot_loaded": True,
        }
        for server in view.hot_loaded
    ]

    healthy_count = view.ready_servers
    total_count = view.total_servers
    replica = view.replica_block()

    return {
        "mcp_servers": mcp_servers_status,
        "runtime_mcp_servers": runtime_status,
        "groups": groups_status,
        "summary": {
            "healthy_mcp_servers": healthy_count,
            "total_mcp_servers": total_count,
            "runtime_mcp_servers": len(runtime_status),
            "runtime_healthy": sum(1 for s in view.hot_loaded if s.state == "ready"),
            "uptime": replica["uptime"],
            "uptime_seconds": replica["uptime_seconds"],
        },
        **view.scope_fields(),
        "formatted": _format_status_dashboard(
            mcp_servers_status + runtime_status,
            groups_status,
            healthy_count,
            total_count,
            replica["uptime"],
            view.instance_id,
        ),
    }


#: One indicator per server lifecycle state, keyed by the enum. `initializing`
#: is shown as `[STARTING]`, the documented name. A state added to the enum
#: without an entry here renders `[?]`, and the test that walks the enum fails.
_SERVER_INDICATORS: dict[McpServerState, str] = {
    McpServerState.COLD: "[COLD]",
    McpServerState.INITIALIZING: "[STARTING]",
    McpServerState.READY: "[READY]",
    McpServerState.DEGRADED: "[DEGRADED]",
    McpServerState.DEAD: "[DEAD]",
}

#: A group's state is a second vocabulary: its availability, computed from its
#: members, not a lifecycle. A group is never "cold"; its members are. So it has
#: its own indicators and never borrows the server ones (#1378).
_GROUP_INDICATORS: dict[GroupState, str] = {
    GroupState.INACTIVE: "[INACTIVE]",
    GroupState.PARTIAL: "[PARTIAL]",
    GroupState.HEALTHY: "[HEALTHY]",
    GroupState.DEGRADED: "[DEGRADED]",
}

#: The frame is never narrower than it was before #1378, so a small fleet looks
#: the same, and never wider than this, so one very long name cannot stretch it.
_FRAME_MIN_INNER = 47
_FRAME_MAX_INNER = 100
#: Widest a column other than the last may grow. Longer cells are elided with `…`.
_COLUMN_MAX = 40
_COLUMN_GAP = "  "
_ELISION = "…"


def _get_status_indicator(state: str) -> str:
    """Indicator for a server lifecycle state; `[?]` only for a string that is not one."""
    try:
        return _SERVER_INDICATORS.get(McpServerState(state.lower()), "[?]")
    except ValueError:
        return "[?]"


def _group_indicator(state: str) -> str:
    """Indicator for a group availability state; `[?]` only for a string that is not one."""
    try:
        return _GROUP_INDICATORS.get(GroupState(state.lower()), "[?]")
    except ValueError:
        return "[?]"


def _format_status_dashboard(
    mcp_servers: list,
    groups: list,
    healthy: int,
    total: int,
    uptime: str,
    instance_id: str,
) -> str:
    """Format status as ASCII dashboard, headed by the replica it describes.

    The replica's name and scope go above the frame rather than inside it: an
    instance id is a pod name plus a suffix and can be longer than a row, and
    it must not be truncated, because the suffix is what tells two replicas
    apart.

    Servers and groups are separate sections with separate columns, because
    their states are separate vocabularies (#1378). The frame is sized to what
    it holds, so every line has the same width and the frame closes.
    """
    sections = [["MCP-Hangar Status (this replica)"]]
    if mcp_servers:
        sections.append(_server_table(mcp_servers))
    if groups:
        sections.append(_group_table(groups))
    sections.append([f"Health: {healthy}/{total} mcp_servers healthy", f"Replica uptime: {uptime}"])
    return "\n".join([f"Answered by replica: {instance_id}", _DASHBOARD_SCOPE_LINE, *_frame(sections)])


def _server_table(servers: list) -> list[str]:
    """The server section: lifecycle indicator, id, state, note."""
    rows = [
        [s["indicator"], s["id"], s["state"], f"last: {s['last_used']}" if "last_used" in s else s.get("note", "")]
        for s in servers
    ]
    return _table(["STATUS", "SERVER", "STATE", "NOTE"], rows)


def _group_table(groups: list) -> list[str]:
    """The group section: id, availability state, members healthy, circuit."""
    rows = [
        [
            g["id"],
            g["state"],
            f"{g['healthy_members']}/{g['total_members']}",
            "open" if g["circuit_open"] else "closed",
        ]
        for g in groups
    ]
    return _table(["GROUP", "STATE", "HEALTHY", "CIRCUIT"], rows)


def _table(header: list[str], rows: list[list[str]]) -> list[str]:
    """Cells aligned in columns, header first.

    Every column but the last is as wide as its widest cell, up to
    `_COLUMN_MAX`. The last is left ragged; the frame fits it.
    """
    table = [header, *rows]
    widths = [min(_COLUMN_MAX, max(_display_width(row[i]) for row in table)) for i in range(len(header) - 1)]
    return [
        _COLUMN_GAP.join([*(_fit(cell, width) for cell, width in zip(row[:-1], widths, strict=True)), row[-1]]).rstrip()
        for row in table
    ]


def _frame(sections: list[list[str]]) -> list[str]:
    """Box the sections, separated by rules, every line the same display width."""
    inner = max(_FRAME_MIN_INNER, min(_FRAME_MAX_INNER, max(_display_width(line) for s in sections for line in s)))
    rule = "─" * (inner + 2)
    lines = [f"╭{rule}╮"]
    for n, section in enumerate(sections):
        if n:
            lines.append(f"├{rule}┤")
        lines.extend(f"│ {_fit(line, inner)} │" for line in section)
    lines.append(f"╰{rule}╯")
    return lines


def _fit(text: str, width: int) -> str:
    """`text` in exactly `width` display columns: padded, or cut and ended with `…`.

    The cut is visible so that a shortened id is never mistaken for a real one.
    """
    if _display_width(text) <= width:
        return text + " " * (width - _display_width(text))
    kept: list[str] = []
    used = 0
    for char in text:
        if used + _char_width(char) > width - 1:
            break
        kept.append(char)
        used += _char_width(char)
    return "".join(kept) + _ELISION + " " * (width - 1 - used)


def _display_width(text: str) -> int:
    """Terminal columns `text` takes: East Asian wide characters two, combining marks none."""
    return sum(_char_width(char) for char in text)


def _char_width(char: str) -> int:
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _validate_mcp_server_name(name: str) -> None:
    """Validate mcp_server name for loading."""
    if not name or not name.strip():
        raise ValueError("McpServer name cannot be empty")
    if len(name) > 128:
        raise ValueError("McpServer name too long (max 128 characters)")


def register_load_tools(mcp: FastMCP) -> None:
    """Register hot-loading tools with MCP server."""

    @mcp.tool(name="hangar_load")
    @mcp_tool_wrapper(
        tool_name="hangar_load",
        rate_limit_key=lambda name, **kwargs: f"hangar_load:{name}",
        check_rate_limit=check_rate_limit,
        validate=lambda name, **kwargs: _validate_mcp_server_name(name),
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def hangar_load(
        name: str,
        force_unverified: bool = False,
        allow_tools: list[str] | None = None,
        deny_tools: list[str] | None = None,
        approval_tools: list[str] | None = None,
    ) -> dict:
        """Load an MCP mcp_server from the official registry at runtime.

        CHOOSE THIS when: you need a capability not in configured mcp_servers.
        CHOOSE hangar_start when: mcp_server is already configured, just needs starting.
        CHOOSE hangar_call when: mcp_server is configured and you want to invoke it directly.
        NOTE: Loaded mcp_servers are ephemeral (lost on restart). Browse: https://mcp.so/servers

        Side effects: Downloads and starts mcp_server process. Adds to runtime registry.

        Args:
            name: str - McpServer name from registry (e.g., "time", "stripe", "mcp-server-github")
            force_unverified: bool - Allow loading unverified mcp_servers (default: false)
            allow_tools: list[str] | None - If set, only these tools are visible (glob patterns supported)
            deny_tools: list[str] | None - If set, these tools are hidden (glob patterns supported)
            approval_tools: list[str] | None - If set, these tools are visible but held for human
                approval before each call (glob patterns supported). Refused when the deployment
                has no approval gate, rather than loading tools that would run unapproved.

        Returns:
            Success: {status: "loaded", mcp_server: str, tools: list[str]}
            Ambiguous: {status: "ambiguous", message: str, matches: list[str]}
            Not found: {status: "not_found", message: str}
            Missing secrets: {status: "missing_secrets", mcp_server_name: str, missing: list[str], instructions: str}
            Unverified: {status: "unverified", mcp_server_name: str, message: str, instructions: str}
            Not configured: {status: "failed", message: str}

        Example:
            hangar_load("time")
            # {"status": "loaded", "mcp_server_id": "mcp-server-time", "tools": ["get_current_time"]}

            hangar_load("grafana", deny_tools=["delete_*", "create_alert_rule"])
            # {"status": "loaded", "mcp_server_id": "grafana", "tools": [...]} (filtered)

            hangar_load("grafana", approval_tools=["silence_*"])
            # {"status": "loaded", ...} -- silence_* is listed, and each call waits for a human

            hangar_load("sql")
            # {"status": "ambiguous", "message": "Multiple mcp_servers match 'sql'",
            #  "matches": ["mcp-server-sqlite", "mcp-server-postgres"]}

            hangar_load("stripe")
            # {"status": "missing_secrets", "missing": ["STRIPE_API_KEY"],
            #  "instructions": "Set STRIPE_API_KEY environment variable"}

            hangar_load("untrusted-tool")
            # {"status": "unverified", "instructions": "Use force_unverified=True to load"}
        """
        ctx = get_context()

        if not hasattr(ctx, "load_mcp_server_handler") or ctx.load_mcp_server_handler is None:
            return {
                "status": "failed",
                "message": "Hot-loading is not configured. Ensure registry client is initialized.",
            }

        command = LoadMcpServerCommand(
            name=name,
            force_unverified=force_unverified,
            user_id=None,
            allow_tools=allow_tools,
            deny_tools=deny_tools,
            approval_tools=approval_tools,
        )

        try:
            # Handler is async, await it directly
            result = await ctx.load_mcp_server_handler.handle(command)
            return result.to_dict()

        except UnverifiedMcpServerError as e:
            return {
                "status": "unverified",
                "mcp_server_name": e.mcp_server_name,
                "message": str(e),
                "instructions": "Use force_unverified=True to load unverified mcp_servers (security risk).",
            }

        except MissingSecretsError as e:
            return {
                "status": "missing_secrets",
                "mcp_server_name": e.mcp_server_name,
                "missing": e.missing,
                "message": str(e),
                "instructions": e.instructions,
            }

        except RegistryServerNotFoundError as e:
            return {
                "status": "not_found",
                "message": f"McpServer '{e.server_id}' not found in the registry.",
            }

        except RegistryAmbiguousSearchError as e:
            return {
                "status": "ambiguous",
                "message": f"Multiple mcp_servers match '{e.query}'. Please be more specific.",
                "matches": e.matches,
            }

    @mcp.tool(name="hangar_unload")
    @mcp_tool_wrapper(
        tool_name="hangar_unload",
        rate_limit_key=lambda mcp_server=None, **kw: f"hangar_unload:{mcp_server}",
        check_rate_limit=check_rate_limit,
        validate=lambda mcp_server=None, **kw: validate_mcp_server_id_input(mcp_server),
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_unload(mcp_server: str) -> dict:
        """Unload a hot-loaded mcp_server.

        CHOOSE THIS when: removing a mcp_server loaded via hangar_load.
        CHOOSE hangar_stop when: stopping a configured mcp_server (will auto-restart on call).
        NOTE: Only works for hot-loaded mcp_servers, not configured ones.

        Side effects: Stops mcp_server process. Removes from runtime registry.

        Args:
            mcp_server: str - McpServer ID (from hangar_load result)

        Returns:
            Success: {status: "unloaded", mcp_server: str, message: str, lifetime_seconds: float}
            Not hot-loaded: {status: "not_hot_loaded", mcp_server: str, message: str}
            Not configured: {status: "failed", message: str}

        Example:
            hangar_unload("mcp-server-time")
            # {"status": "unloaded", "mcp_server": "mcp-server-time",
            #  "message": "Successfully unloaded 'mcp-server-time'", "lifetime_seconds": 3600}

            hangar_unload("math")
            # {"status": "not_hot_loaded", "mcp_server": "math",
            #  "message": "McpServer 'math' was not hot-loaded. Use hangar_stop for configured mcp_servers."}
        """
        ctx = get_context()

        if not hasattr(ctx, "unload_mcp_server_handler") or ctx.unload_mcp_server_handler is None:
            return {
                "status": "failed",
                "message": "Hot-loading is not configured.",
            }

        command = UnloadMcpServerCommand(
            mcp_server_id=mcp_server,
            user_id=None,
        )

        try:
            result = ctx.unload_mcp_server_handler.handle(command)
            return {
                "status": "unloaded",
                "mcp_server": mcp_server,
                "message": f"Successfully unloaded '{mcp_server}'",
                "lifetime_seconds": result.get("lifetime_seconds", 0),
            }

        except McpServerNotHotLoadedError:
            return {
                "status": "not_hot_loaded",
                "mcp_server": mcp_server,
                "message": f"McpServer '{mcp_server}' was not hot-loaded. Use hangar_stop for configured mcp_servers.",
            }

    @mcp.tool(name="hangar_reload_config")
    @mcp_tool_wrapper(
        tool_name="hangar_reload_config",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_reload_config"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _hangar_reload_config(graceful: bool = True) -> dict:
        """Reload configuration from file and apply changes.

        CHOOSE THIS when: you modified config.yaml and want to apply changes without restarting.
        NOTE: Preserves unchanged mcp_servers (no restart), only affects added/removed/updated.

        Side effects: Stops/starts mcp_servers based on configuration changes.

        Args:
            graceful: bool - If True, wait for idle state before stopping (default: true)

        Returns:
            {
                status: "success"|"failed",
                message: str,
                mcp_servers_added: [str],
                mcp_servers_removed: [str],
                mcp_servers_updated: [str],
                mcp_servers_unchanged: [str],
                duration_ms: float
            }

        Example:
            hangar_reload_config()
            # {"status": "success", "message": "Configuration reloaded successfully",
            #  "mcp_servers_added": ["new-mcp_server"], "mcp_servers_removed": [],
            #  "mcp_servers_updated": ["modified-mcp_server"], "mcp_servers_unchanged": ["stable-mcp_server"],
            #  "duration_ms": 123.45}

            hangar_reload_config(graceful=false)
            # Immediate reload without waiting for idle state
        """
        return hangar_reload_config(graceful)


def hangar_reload_config(graceful: bool = True) -> dict:
    """
    Reload configuration from file and apply changes to running mcp_servers.

    This is a COMMAND operation that:
    - Adds new mcp_servers from config
    - Removes deleted mcp_servers
    - Restarts mcp_servers with modified configuration
    - Preserves unchanged mcp_servers (no restart)

    Args:
        graceful: If True, wait for idle state before stopping mcp_servers.
                  If False, immediately stop mcp_servers.

    Returns:
        Dictionary with reload status and statistics
    """
    ctx = get_context()

    command = ReloadConfigurationCommand(
        graceful=graceful,
        requested_by="tool",
    )

    try:
        result = ctx.runtime.command_bus.send(command)
        return {
            "status": "success",
            "message": "Configuration reloaded successfully",
            "mcp_servers_added": result.get("mcp_servers_added", []),
            "mcp_servers_removed": result.get("mcp_servers_removed", []),
            "mcp_servers_updated": result.get("mcp_servers_updated", []),
            "mcp_servers_unchanged": result.get("mcp_servers_unchanged", []),
            "duration_ms": result.get("duration_ms", 0),
        }

    except Exception as e:  # noqa: BLE001 -- fault-barrier: reload failure must return error result, not crash MCP tool
        return {
            "status": "failed",
            "message": f"Configuration reload failed: {str(e)}",
            "error_type": type(e).__name__,
        }
