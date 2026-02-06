"""Control plane management tools: list, start, stop, status, load, unload.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import time

from mcp.server.fastmcp import FastMCP

from ...application.commands import (
    LoadProviderCommand,
    ReloadConfigurationCommand,
    StartProviderCommand,
    StopProviderCommand,
    UnloadProviderCommand,
)
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...application.queries import ListProvidersQuery
from ...domain.exceptions import (
    MissingSecretsError,
    ProviderNotHotLoadedError,
    RegistryAmbiguousSearchError,
    RegistryServerNotFoundError,
    UnverifiedProviderError,
)
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input

# Server start time for uptime calculation
_server_start_time: float = time.time()


def hangar_list(state_filter: str | None = None) -> dict:
    """
    List all managed providers and groups with lifecycle state and metadata.

    This is a QUERY operation - no side effects, only reads data.

    Args:
        state_filter: Optional filter by state (cold, ready, degraded, dead)

    Returns:
        Dictionary with 'providers', 'groups', and 'runtime_providers' keys
    """
    from ..state import get_runtime_providers

    ctx = get_context()

    # Query via CQRS query bus
    query = ListProvidersQuery(state_filter=state_filter)
    summaries = ctx.query_bus.execute(query)

    # Read groups from context
    groups_list = []
    for group_id, group in ctx.groups.items():
        group_info = group.to_status_dict()
        if state_filter and group_info.get("state") != state_filter:
            continue
        groups_list.append(group_info)

    # Read runtime (hot-loaded) providers
    runtime_store = get_runtime_providers()
    runtime_providers_list = []
    for provider, metadata in runtime_store.list_all():
        provider_state = provider.state.value if hasattr(provider, "state") else "unknown"
        if state_filter and provider_state != state_filter:
            continue
        runtime_providers_list.append(
            {
                "provider": str(provider.provider_id),
                "state": provider_state,
                "source": metadata.source,
                "verified": metadata.verified,
                "ephemeral": metadata.ephemeral,
                "loaded_at": metadata.loaded_at.isoformat(),
                "lifetime_seconds": round(metadata.lifetime_seconds(), 1),
            }
        )

    return {
        "providers": [s.to_dict() for s in summaries],
        "groups": groups_list,
        "runtime_providers": runtime_providers_list,
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
        """List all providers and groups with precise numeric values.

        CHOOSE THIS when: you need exact data for processing, filtering, or automation.
        CHOOSE hangar_status when: you need human-readable dashboard with visual indicators.
        CHOOSE hangar_group_list when: you need member-level details (rotation, weights).

        Side effects: None (read-only).

        Args:
            state_filter: str - Filter by state: "cold", "ready", "degraded", "dead" (default: null)

        Returns:
            {
                providers: [{
                    provider: str,
                    state: str,
                    mode: str,
                    alive: bool,
                    tools_count: int,
                    health_status: str,
                    tools_predefined: bool,
                    description?: str
                }],
                groups: [{group_id, state, strategy, healthy_count, total_members, ...}],
                runtime_providers: [{
                    provider: str,
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
            # {"providers": [{"provider": "math", "state": "ready", "mode": "subprocess",
            #   "alive": true, "tools_count": 2, "health_status": "healthy"}],
            #  "groups": [], "runtime_providers": []}

            hangar_list(state_filter="ready")
            # Returns only providers/groups in "ready" state

            hangar_list(state_filter="cold")
            # {"providers": [{"provider": "sqlite", "state": "cold", "alive": false}], ...}
        """
        return hangar_list(state_filter)

    @mcp.tool(name="hangar_start")
    @mcp_tool_wrapper(
        tool_name="hangar_start",
        rate_limit_key=lambda provider: f"hangar_start:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_start(provider: str) -> dict:
        """Start a provider or all members of a group.

        CHOOSE THIS when: you need to verify startup works or pre-warm a specific provider/group.
        CHOOSE hangar_warm when: you need to pre-warm multiple providers at once.
        CHOOSE hangar_call when: you want to invoke a tool (auto-starts cold providers).
        SKIP THIS when: you just want to call a tool - hangar_call auto-starts providers.

        Side effects: Starts provider process/container. State changes from cold to ready.

        Args:
            provider: str - Provider ID or Group ID

        Returns:
            Provider: {provider: str, state: str, tools: list[str]}
            Group: {group: str, state: str, members_started: int, healthy_count: int, total_members: int}
            Error: ValueError with "unknown_provider: <id>" or "unknown_group: <id>"

        Example:
            hangar_start("math")
            # {"provider": "math", "state": "ready", "tools": ["add", "multiply"]}

            hangar_start("llm-group")
            # {"group": "llm-group", "state": "ready", "members_started": 2,
            #  "healthy_count": 2, "total_members": 3}

            hangar_start("unknown")
            # Error: unknown_provider: unknown
        """
        ctx = get_context()

        # Check if it's a group first
        if ctx.group_exists(provider):
            group = ctx.get_group(provider)
            started = group.start_all()
            return {
                "group": provider,
                "state": group.state.value,
                "members_started": started,
                "healthy_count": group.healthy_count,
                "total_members": group.total_count,
            }

        # Check provider exists
        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        # Send command via CQRS command bus
        command = StartProviderCommand(provider_id=provider)
        return ctx.command_bus.send(command)

    @mcp.tool(name="hangar_stop")
    @mcp_tool_wrapper(
        tool_name="hangar_stop",
        rate_limit_key=lambda provider: f"hangar_stop:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def hangar_stop(provider: str) -> dict:
        """Stop a provider or all members of a group.

        CHOOSE THIS when: you need to force restart or free resources immediately.
        CHOOSE hangar_unload when: removing a hot-loaded provider permanently.
        SKIP THIS when: "cleaning up" between calls - providers auto-manage via idle_ttl.

        Side effects: Stops provider process/container. State changes to cold.

        Args:
            provider: str - Provider ID or Group ID

        Returns:
            Provider: {stopped: str, reason: str}
            Group: {group: str, state: str, stopped: bool}
            Error: ValueError with "unknown_provider: <id>"

        Example:
            hangar_stop("math")
            # {"stopped": "math", "reason": "manual"}

            hangar_stop("llm-group")
            # {"group": "llm-group", "state": "cold", "stopped": true}

            hangar_stop("unknown")
            # Error: unknown_provider: unknown
        """
        ctx = get_context()

        # Check if it's a group first
        if ctx.group_exists(provider):
            group = ctx.get_group(provider)
            group.stop_all()
            return {
                "group": provider,
                "state": group.state.value,
                "stopped": True,
            }

        # Check provider exists
        if not ctx.provider_exists(provider):
            raise ValueError(f"unknown_provider: {provider}")

        # Send command via CQRS command bus
        command = StopProviderCommand(provider_id=provider)
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
                providers: [{id: str, indicator: str, state: str, mode: str, last_used?: str}],
                groups: [{id: str, indicator: str, state: str, healthy_members: int, total_members: int}],
                runtime_providers: [{id: str, indicator: str, state: str, source: str, verified: bool}],
                summary: {healthy_providers: int, total_providers: int, uptime: str, uptime_seconds: float},
                formatted: str
            }
            Indicator values: [READY], [COLD], [STARTING], [DEGRADED], [DEAD]

        Example:
            hangar_status()
            # {"providers": [{"id": "math", "indicator": "[READY]", "state": "ready", "mode": "subprocess"}],
            #  "groups": [], "runtime_providers": [],
            #  "summary": {"healthy_providers": 1, "total_providers": 1, "uptime": "2h 15m"},
            #  "formatted": "...ASCII dashboard..."}
        """
        ctx = get_context()

        # Get all providers
        query = ListProvidersQuery(state_filter=None)
        summaries = ctx.query_bus.execute(query)

        # Format providers with status indicators
        providers_status = []
        healthy_count = 0
        total_count = len(summaries)

        for summary in summaries:
            state = summary.state
            indicator = _get_status_indicator(state)

            provider_info = {
                "id": summary.provider_id,
                "indicator": indicator,
                "state": state,
                "mode": summary.mode,
            }

            # Add additional context based on state
            if state == "ready":
                healthy_count += 1
                if hasattr(summary, "last_used_ago_s"):
                    provider_info["last_used"] = _format_time_ago(summary.last_used_ago_s)
            elif state == "cold":
                provider_info["note"] = "Will start on first request"
            elif state == "degraded":
                if hasattr(summary, "consecutive_failures"):
                    provider_info["consecutive_failures"] = summary.consecutive_failures

            providers_status.append(provider_info)

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

        # Get runtime (hot-loaded) providers
        from ..state import get_runtime_providers

        runtime_store = get_runtime_providers()
        runtime_status = []
        runtime_healthy = 0
        for provider, metadata in runtime_store.list_all():
            state = provider.state.value if hasattr(provider, "state") else "unknown"
            indicator = _get_status_indicator(state)
            if state == "ready":
                runtime_healthy += 1
                healthy_count += 1

            runtime_info = {
                "id": str(provider.provider_id),
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
            "providers": providers_status,
            "runtime_providers": runtime_status,
            "groups": groups_status,
            "summary": {
                "healthy_providers": healthy_count,
                "total_providers": total_count,
                "runtime_providers": len(runtime_status),
                "runtime_healthy": runtime_healthy,
                "uptime": uptime_formatted,
                "uptime_seconds": round(uptime_s, 1),
            },
            "formatted": _format_status_dashboard(
                providers_status + runtime_status, groups_status, healthy_count, total_count, uptime_formatted
            ),
        }


def _get_status_indicator(state: str) -> str:
    """Get visual indicator for provider state."""
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
    providers: list,
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

    # Providers
    for p in providers:
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
    lines.append(f"│ Health: {healthy}/{total} providers healthy".ljust(50) + "│")
    lines.append(f"│ Uptime: {uptime}".ljust(50) + "│")
    lines.append("╰─────────────────────────────────────────────────╯")

    return "\n".join(lines)


def _validate_provider_name(name: str) -> None:
    """Validate provider name for loading."""
    if not name or not name.strip():
        raise ValueError("Provider name cannot be empty")
    if len(name) > 128:
        raise ValueError("Provider name too long (max 128 characters)")


def register_load_tools(mcp: FastMCP) -> None:
    """Register hot-loading tools with MCP server."""

    @mcp.tool(name="hangar_load")
    @mcp_tool_wrapper(
        tool_name="hangar_load",
        rate_limit_key=lambda name, **kwargs: f"hangar_load:{name}",
        check_rate_limit=check_rate_limit,
        validate=lambda name, **kwargs: _validate_provider_name(name),
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    async def hangar_load(name: str, force_unverified: bool = False) -> dict:
        """Load an MCP provider from the official registry at runtime.

        CHOOSE THIS when: you need a capability not in configured providers.
        CHOOSE hangar_start when: provider is already configured, just needs starting.
        CHOOSE hangar_call when: provider is configured and you want to invoke it directly.
        NOTE: Loaded providers are ephemeral (lost on restart). Browse: https://mcp.so/servers

        Side effects: Downloads and starts provider process. Adds to runtime registry.

        Args:
            name: str - Provider name from registry (e.g., "time", "stripe", "mcp-server-github")
            force_unverified: bool - Allow loading unverified providers (default: false)

        Returns:
            Success: {status: "loaded", provider: str, tools: list[str]}
            Ambiguous: {status: "ambiguous", message: str, matches: list[str]}
            Not found: {status: "not_found", message: str}
            Missing secrets: {status: "missing_secrets", provider_name: str, missing: list[str], instructions: str}
            Unverified: {status: "unverified", provider_name: str, message: str, instructions: str}
            Not configured: {status: "failed", message: str}

        Example:
            hangar_load("time")
            # {"status": "loaded", "provider_id": "mcp-server-time", "tools": ["get_current_time"]}

            hangar_load("sql")
            # {"status": "ambiguous", "message": "Multiple providers match 'sql'",
            #  "matches": ["mcp-server-sqlite", "mcp-server-postgres"]}

            hangar_load("stripe")
            # {"status": "missing_secrets", "missing": ["STRIPE_API_KEY"],
            #  "instructions": "Set STRIPE_API_KEY environment variable"}

            hangar_load("untrusted-tool")
            # {"status": "unverified", "instructions": "Use force_unverified=True to load"}
        """
        ctx = get_context()

        if not hasattr(ctx, "load_provider_handler") or ctx.load_provider_handler is None:
            return {
                "status": "failed",
                "message": "Hot-loading is not configured. Ensure registry client is initialized.",
            }

        command = LoadProviderCommand(
            name=name,
            force_unverified=force_unverified,
            user_id=None,
        )

        try:
            # Handler is async, await it directly
            result = await ctx.load_provider_handler.handle(command)
            return result.to_dict()

        except UnverifiedProviderError as e:
            return {
                "status": "unverified",
                "provider_name": e.provider_name,
                "message": str(e),
                "instructions": "Use force_unverified=True to load unverified providers (security risk).",
            }

        except MissingSecretsError as e:
            return {
                "status": "missing_secrets",
                "provider_name": e.provider_name,
                "missing": e.missing,
                "message": str(e),
                "instructions": e.instructions,
            }

        except RegistryServerNotFoundError as e:
            return {
                "status": "not_found",
                "message": f"Provider '{e.server_id}' not found in the registry.",
            }

        except RegistryAmbiguousSearchError as e:
            return {
                "status": "ambiguous",
                "message": f"Multiple providers match '{e.query}'. Please be more specific.",
                "matches": e.matches,
            }

    @mcp.tool(name="hangar_unload")
    @mcp_tool_wrapper(
        tool_name="hangar_unload",
        rate_limit_key=lambda provider=None, **kw: f"hangar_unload:{provider}",
        check_rate_limit=check_rate_limit,
        validate=lambda provider=None, **kw: validate_provider_id_input(provider),
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_unload(provider: str) -> dict:
        """Unload a hot-loaded provider.

        CHOOSE THIS when: removing a provider loaded via hangar_load.
        CHOOSE hangar_stop when: stopping a configured provider (will auto-restart on call).
        NOTE: Only works for hot-loaded providers, not configured ones.

        Side effects: Stops provider process. Removes from runtime registry.

        Args:
            provider: str - Provider ID (from hangar_load result)

        Returns:
            Success: {status: "unloaded", provider: str, message: str, lifetime_seconds: float}
            Not hot-loaded: {status: "not_hot_loaded", provider: str, message: str}
            Not configured: {status: "failed", message: str}

        Example:
            hangar_unload("mcp-server-time")
            # {"status": "unloaded", "provider": "mcp-server-time",
            #  "message": "Successfully unloaded 'mcp-server-time'", "lifetime_seconds": 3600}

            hangar_unload("math")
            # {"status": "not_hot_loaded", "provider": "math",
            #  "message": "Provider 'math' was not hot-loaded. Use hangar_stop for configured providers."}
        """
        ctx = get_context()

        if not hasattr(ctx, "unload_provider_handler") or ctx.unload_provider_handler is None:
            return {
                "status": "failed",
                "message": "Hot-loading is not configured.",
            }

        command = UnloadProviderCommand(
            provider_id=provider,
            user_id=None,
        )

        try:
            result = ctx.unload_provider_handler.handle(command)
            return {
                "status": "unloaded",
                "provider": provider,
                "message": f"Successfully unloaded '{provider}'",
                "lifetime_seconds": result.get("lifetime_seconds", 0),
            }

        except ProviderNotHotLoadedError:
            return {
                "status": "not_hot_loaded",
                "provider": provider,
                "message": f"Provider '{provider}' was not hot-loaded. Use hangar_stop for configured providers.",
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
        NOTE: Preserves unchanged providers (no restart), only affects added/removed/updated.

        Side effects: Stops/starts providers based on configuration changes.

        Args:
            graceful: bool - If True, wait for idle state before stopping (default: true)

        Returns:
            {
                status: "success"|"failed",
                message: str,
                providers_added: [str],
                providers_removed: [str],
                providers_updated: [str],
                providers_unchanged: [str],
                duration_ms: float
            }

        Example:
            hangar_reload_config()
            # {"status": "success", "message": "Configuration reloaded successfully",
            #  "providers_added": ["new-provider"], "providers_removed": [],
            #  "providers_updated": ["modified-provider"], "providers_unchanged": ["stable-provider"],
            #  "duration_ms": 123.45}

            hangar_reload_config(graceful=false)
            # Immediate reload without waiting for idle state
        """
        return hangar_reload_config(graceful)


def hangar_reload_config(graceful: bool = True) -> dict:
    """
    Reload configuration from file and apply changes to running providers.

    This is a COMMAND operation that:
    - Adds new providers from config
    - Removes deleted providers
    - Restarts providers with modified configuration
    - Preserves unchanged providers (no restart)

    Args:
        graceful: If True, wait for idle state before stopping providers.
                  If False, immediately stop providers.

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
            "providers_added": result.get("providers_added", []),
            "providers_removed": result.get("providers_removed", []),
            "providers_updated": result.get("providers_updated", []),
            "providers_unchanged": result.get("providers_unchanged", []),
            "duration_ms": result.get("duration_ms", 0),
        }

    except Exception as e:
        return {
            "status": "failed",
            "message": f"Configuration reload failed: {str(e)}",
            "error_type": type(e).__name__,
        }
