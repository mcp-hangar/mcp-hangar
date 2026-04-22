"""Control plane management tools: list, start, stop, status, load, unload.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import time

from mcp.server.fastmcp import FastMCP

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
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_mcp_server_id_input

# Server start time for uptime calculation
_server_start_time: float = time.time()


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


def register_hangar_tools(mcp: FastMCP) -> None:
    """Register control plane management tools with MCP server."""

    @mcp.tool(name="hangar_list")
    @mcp_tool_wrapper(
        tool_name="hangar_list",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_list"),
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
            Group: {group: str, state: str, members_started: int, healthy_count: int, total_members: int}
            Error: ValueError with "unknown_mcp_server: <id>" or "unknown_group: <id>"

        Example:
            hangar_start("math")
            # {"mcp_server": "math", "state": "ready", "tools": ["add", "multiply"]}

            hangar_start("llm-group")
            # {"group": "llm-group", "state": "ready", "members_started": 2,
            #  "healthy_count": 2, "total_members": 3}

            hangar_start("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        # Check if it's a group first
        if ctx.group_exists(mcp_server):
            group = ctx.get_group(mcp_server)
            started = group.start_all()
            return {
                "group": mcp_server,
                "state": group.state.value,
                "members_started": started,
                "healthy_count": group.healthy_count,
                "total_members": group.total_count,
            }

        # Check mcp_server exists
        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        # Send command via CQRS command bus
        command = StartMcpServerCommand(mcp_server_id=mcp_server)
        return ctx.command_bus.send(command)

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
        return ctx.command_bus.send(command)

    @mcp.tool(name="hangar_status")
    @mcp_tool_wrapper(
        tool_name="hangar_status",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_status"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_status() -> dict:
        """Get human-readable status dashboard of the registry.

        CHOOSE THIS when: you need to display status to user or quick health overview.
        CHOOSE hangar_list when: you need exact values for processing or filtering.
        CHOOSE hangar_health when: you need system health with security metrics.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            {
                mcp_servers: [{id: str, indicator: str, state: str, mode: str, last_used?: str}],
                groups: [{id: str, indicator: str, state: str, healthy_members: int, total_members: int}],
                runtime_mcp_servers: [{id: str, indicator: str, state: str, source: str, verified: bool}],
                summary: {healthy_mcp_servers: int, total_mcp_servers: int, uptime: str, uptime_seconds: float},
                formatted: str
            }
            Indicator values: [READY], [COLD], [STARTING], [DEGRADED], [DEAD]

        Example:
            hangar_status()
            # {"mcp_servers": [{"id": "math", "indicator": "[READY]", "state": "ready", "mode": "subprocess"}],
            #  "groups": [], "runtime_mcp_servers": [],
            #  "summary": {"healthy_mcp_servers": 1, "total_mcp_servers": 1, "uptime": "2h 15m"},
            #  "formatted": "...ASCII dashboard..."}
        """
        ctx = get_context()

        # Get all mcp_servers
        query = ListMcpServersQuery(state_filter=None)
        summaries = ctx.query_bus.execute(query)

        # Format mcp_servers with status indicators
        mcp_servers_status = []
        healthy_count = 0
        total_count = len(summaries)

        for summary in summaries:
            state = summary.state
            indicator = _get_status_indicator(state)

            mcp_server_info = {
                "id": summary.mcp_server_id,
                "indicator": indicator,
                "state": state,
                "mode": summary.mode,
            }

            # Add additional context based on state
            if state == "ready":
                healthy_count += 1
                if hasattr(summary, "last_used_ago_s"):
                    mcp_server_info["last_used"] = _format_time_ago(summary.last_used_ago_s)
            elif state == "cold":
                mcp_server_info["note"] = "Will start on first request"
            elif state == "degraded":
                if hasattr(summary, "consecutive_failures"):
                    mcp_server_info["consecutive_failures"] = summary.consecutive_failures

            mcp_servers_status.append(mcp_server_info)

        # Get groups
        groups_status = []
        for group_id, group in ctx.groups.items():
            group_info = {
                "id": group_id,
                "indicator": _get_status_indicator(group.state.value),
                "state": group.state.value,
                "healthy_members": group.healthy_count,
                "total_members": group.total_count,
            }
            groups_status.append(group_info)

        # Get runtime (hot-loaded) mcp_servers
        from ..state import get_runtime_mcp_servers

        runtime_store = get_runtime_mcp_servers()
        runtime_status = []
        runtime_healthy = 0
        for mcp_server, metadata in runtime_store.list_all():
            state = mcp_server.state.value if hasattr(mcp_server, "state") else "unknown"
            indicator = _get_status_indicator(state)
            if state == "ready":
                runtime_healthy += 1
                healthy_count += 1

            runtime_info = {
                "id": str(mcp_server.mcp_server_id),
                "indicator": indicator,
                "state": state,
                "source": metadata.source,
                "verified": metadata.verified,
                "hot_loaded": True,
            }
            runtime_status.append(runtime_info)
            total_count += 1

        # Calculate uptime
        uptime_s = time.time() - _server_start_time
        uptime_formatted = _format_uptime(uptime_s)

        return {
            "mcp_servers": mcp_servers_status,
            "runtime_mcp_servers": runtime_status,
            "groups": groups_status,
            "summary": {
                "healthy_mcp_servers": healthy_count,
                "total_mcp_servers": total_count,
                "runtime_mcp_servers": len(runtime_status),
                "runtime_healthy": runtime_healthy,
                "uptime": uptime_formatted,
                "uptime_seconds": round(uptime_s, 1),
            },
            "formatted": _format_status_dashboard(
                mcp_servers_status + runtime_status, groups_status, healthy_count, total_count, uptime_formatted
            ),
        }


def _get_status_indicator(state: str) -> str:
    """Get visual indicator for mcp_server state."""
    indicators = {
        "ready": "[READY]",
        "cold": "[COLD]",
        "starting": "[STARTING]",
        "degraded": "[DEGRADED]",
        "dead": "[DEAD]",
        "error": "[ERROR]",
    }
    return indicators.get(state.lower(), "[?]")


def _format_time_ago(seconds: float) -> str:
    """Format seconds as human-readable 'time ago' string."""
    if seconds < 60:
        return f"{int(seconds)}s ago"
    elif seconds < 3600:
        return f"{int(seconds / 60)}m ago"
    else:
        return f"{int(seconds / 3600)}h ago"


def _format_uptime(seconds: float) -> str:
    """Format uptime as human-readable string."""
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _format_status_dashboard(
    mcp_servers: list,
    groups: list,
    healthy: int,
    total: int,
    uptime: str,
) -> str:
    """Format status as ASCII dashboard."""
    lines = [
        "╭─────────────────────────────────────────────────╮",
        "│ MCP-Hangar Status                               │",
        "├─────────────────────────────────────────────────┤",
    ]

    # McpServers
    for p in mcp_servers:
        indicator = p["indicator"]
        name = p["id"][:15].ljust(15)
        state = p["state"][:8].ljust(8)
        extra = ""
        if "last_used" in p:
            extra = f"last: {p['last_used']}"
        elif "note" in p:
            extra = p["note"][:20]
        line = f"│ {indicator} {name} {state} {extra[:22].ljust(22)}│"
        lines.append(line)

    # Groups
    for g in groups:
        indicator = g["indicator"]
        name = g["id"][:15].ljust(15)
        state = g["state"][:8].ljust(8)
        extra = f"{g['healthy_members']}/{g['total_members']} healthy"
        line = f"│ {indicator} {name} {state} {extra[:22].ljust(22)}│"
        lines.append(line)

    lines.append("├─────────────────────────────────────────────────┤")
    lines.append(f"│ Health: {healthy}/{total} mcp_servers healthy".ljust(50) + "│")
    lines.append(f"│ Uptime: {uptime}".ljust(50) + "│")
    lines.append("╰─────────────────────────────────────────────────╯")

    return "\n".join(lines)


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
