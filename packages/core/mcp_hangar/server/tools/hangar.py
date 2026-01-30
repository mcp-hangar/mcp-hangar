"""Control plane management tools: list, start, stop, status, load, unload.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import time

from mcp.server.fastmcp import FastMCP

from ...application.commands import (
    LoadProviderCommand,
    StartProviderCommand,
    StopProviderCommand,
    UnloadProviderCommand,
)
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...domain.exceptions import (
    MissingSecretsError,
    ProviderNotHotLoadedError,
    RegistryAmbiguousSearchError,
    RegistryServerNotFoundError,
    UnverifiedProviderError,
)
from ...infrastructure.query_bus import ListProvidersQuery
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
                "provider_id": str(provider.provider_id),
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
        """
        Explicitly start a provider or all members of a group.

        This is a COMMAND operation - it changes state.

        Args:
            provider: Provider ID or Group ID to start

        Returns:
            Dictionary with provider/group state and tools

        Raises:
            ValueError: If provider ID is unknown or invalid
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
        """
        Explicitly stop a provider or all members of a group.

        This is a COMMAND operation - it changes state.

        Args:
            provider: Provider ID or Group ID to stop

        Returns:
            Confirmation dictionary

        Raises:
            ValueError: If provider ID is unknown or invalid
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
        """
        Get a comprehensive status overview of the MCP Registry.

        Shows status of all providers with visual indicators:
        - ✅ ready: Provider is running and healthy
        - ⏸️  idle: Provider is cold, will start on first request
        - 🔄 starting: Provider is starting up
        - ❌ error: Provider has errors or is degraded

        Returns:
            Dictionary with providers, groups, health summary, and uptime
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
        "cold": "[IDLE]",
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
        """
        Load an MCP provider from the official registry at runtime.

        This allows you to dynamically add new provider capabilities without
        restarting the server. Loaded providers are ephemeral and will not
        persist across server restarts.

        Args:
            name: Provider name or ID from the registry (e.g., "mcp-server-time", "stripe").
            force_unverified: Set to True to load providers that are not officially verified.
                              Unverified providers may pose security risks.

        Returns:
            Dictionary with load result including:
            - status: "loaded", "already_loaded", "failed", or "missing_secrets"
            - provider_id: The provider ID if loaded
            - tools: List of available tools if loaded
            - message: Human-readable status message
            - instructions: Setup instructions if secrets are missing

        Examples:
            # Load an official provider
            hangar_load("mcp-server-time")

            # Load with required secrets already set
            hangar_load("stripe")  # Requires STRIPE_API_KEY env var

            # Force load an unverified provider (use with caution)
            hangar_load("my-custom-provider", force_unverified=True)
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
        rate_limit_key=lambda provider_id=None, **kw: f"hangar_unload:{provider_id}",
        check_rate_limit=check_rate_limit,
        validate=lambda provider_id=None, **kw: validate_provider_id_input(provider_id),
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_unload(provider_id: str) -> dict:
        """
        Unload a hot-loaded provider.

        This removes a provider that was loaded at runtime via hangar_load.
        The provider will be stopped and its resources cleaned up.

        Note: Only hot-loaded providers can be unloaded. Providers defined in
        the configuration file cannot be unloaded - use hangar_stop instead.

        Args:
            provider_id: The ID of the provider to unload.

        Returns:
            Dictionary with unload result including:
            - status: "unloaded" or error status
            - provider_id: The provider ID
            - lifetime_seconds: How long the provider was loaded

        Raises:
            ValueError: If the provider was not hot-loaded.
        """
        ctx = get_context()

        if not hasattr(ctx, "unload_provider_handler") or ctx.unload_provider_handler is None:
            return {
                "status": "failed",
                "message": "Hot-loading is not configured.",
            }

        command = UnloadProviderCommand(
            provider_id=provider_id,
            user_id=None,
        )

        try:
            result = ctx.unload_provider_handler.handle(command)
            return {
                "status": "unloaded",
                "provider_id": provider_id,
                "message": f"Successfully unloaded '{provider_id}'",
                "lifetime_seconds": result.get("lifetime_seconds", 0),
            }

        except ProviderNotHotLoadedError:
            return {
                "status": "not_hot_loaded",
                "provider_id": provider_id,
                "message": f"Provider '{provider_id}' was not hot-loaded. Use hangar_stop for configured providers.",
            }
