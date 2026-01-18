"""Group management tools.

Uses ApplicationContext for dependency injection (DIP).
"""

from mcp.server.fastmcp import FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input


def register_group_tools(mcp: FastMCP) -> None:
    """Register group management tools with MCP server."""

    @mcp.tool(name="registry_group_list")
    @mcp_tool_wrapper(
        tool_name="registry_group_list",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("registry_group_list"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def registry_group_list() -> dict:
        """
        List all provider groups with detailed status.

        This is a QUERY operation - read only.

        Returns:
            Dictionary with 'groups' key containing list of group info
        """
        ctx = get_context()
        return {"groups": [group.to_status_dict() for group in ctx.groups.values()]}

    @mcp.tool(name="registry_group_rebalance")
    @mcp_tool_wrapper(
        tool_name="registry_group_rebalance",
        rate_limit_key=lambda group: f"registry_group_rebalance:{group}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def registry_group_rebalance(group: str) -> dict:
        """
        Manually trigger rebalancing for a group.

        This is a COMMAND operation - it changes state.

        Args:
            group: Group ID to rebalance

        Returns:
            Dictionary with group status after rebalancing

        Raises:
            ValueError: If group ID is unknown
        """
        ctx = get_context()

        if not ctx.group_exists(group):
            raise ValueError(f"unknown_group: {group}")

        g = ctx.get_group(group)
        g.rebalance()

        return {
            "group_id": group,
            "state": g.state.value,
            "healthy_count": g.healthy_count,
            "total_members": g.total_count,
            "members_in_rotation": [m.id for m in g.members if m.in_rotation],
        }
