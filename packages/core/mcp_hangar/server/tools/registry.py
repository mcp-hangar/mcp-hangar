"""Registry management tools: list, start, stop, status.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

import time

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartProviderCommand, StopProviderCommand
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...infrastructure.query_bus import ListProvidersQuery
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input

# Server start time for uptime calculation
_server_start_time: float = time.time()


def registry_list(state_filter: str | None = None) -> dict:
    """
    List all providers and groups with status and metadata.

    This is a QUERY operation - no side effects, only reads data.

    Args:
        state_filter: Optional filter by state (cold, ready, degraded, dead)

    Returns:
        Dictionary with 'providers' and 'groups' keys
    """
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

    return {
        "providers": [s.to_dict() for s in summaries],
        "groups": groups_list,
    }


def register_registry_tools(mcp: FastMCP) -> None:
    """Register registry management tools with MCP server."""

    @mcp.tool(name="registry_list")
    @mcp_tool_wrapper(
        tool_name="registry_list",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_list"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def _registry_list(state_filter: str | None = None) -> dict:
        return registry_list(state_filter)

    @mcp.tool(name="registry_start")
    @mcp_tool_wrapper(
        tool_name="registry_start",
        rate_limit_key=lambda provider: f"registry_start:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def registry_start(provider: str) -> dict:
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

    @mcp.tool(name="registry_stop")
    @mcp_tool_wrapper(
        tool_name="registry_stop",
        rate_limit_key=lambda provider: f"registry_stop:{provider}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def registry_stop(provider: str) -> dict:
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

    @mcp.tool(name="registry_status")
    @mcp_tool_wrapper(
        tool_name="registry_status",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_status"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_status() -> dict:
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

        # Calculate uptime
        uptime_s = time.time() - _server_start_time
        uptime_formatted = _format_uptime(uptime_s)

        return {
            "providers": providers_status,
            "groups": groups_status,
            "summary": {
                "healthy_providers": healthy_count,
                "total_providers": total_count,
                "uptime": uptime_formatted,
                "uptime_seconds": round(uptime_s, 1),
            },
            "formatted": _format_status_dashboard(
                providers_status, groups_status, healthy_count, total_count, uptime_formatted
            ),
        }


def _get_status_indicator(state: str) -> str:
    """Get visual indicator for provider state."""
    indicators = {
        "ready": "✅",
        "cold": "⏸️",
        "starting": "🔄",
        "degraded": "⚠️",
        "dead": "❌",
        "error": "❌",
    }
    return indicators.get(state.lower(), "❓")


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
