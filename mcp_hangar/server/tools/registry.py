"""Registry management tools: list, start, stop.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.
"""

from typing import Optional

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartProviderCommand, StopProviderCommand
from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ...infrastructure.query_bus import ListProvidersQuery
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input


def registry_list(state_filter: Optional[str] = None) -> dict:
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
    def _registry_list(state_filter: Optional[str] = None) -> dict:
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
