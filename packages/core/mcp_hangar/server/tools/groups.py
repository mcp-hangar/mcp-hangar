"""Group management tools.

Uses ApplicationContext for dependency injection (DIP).
"""

from mcp.server.fastmcp import FastMCP

from ...application.mcp.tooling import key_global, mcp_tool_wrapper
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_provider_id_input


def register_group_tools(mcp: FastMCP) -> None:
    """Register group management tools with MCP server."""

    @mcp.tool(name="hangar_group_list")
    @mcp_tool_wrapper(
        tool_name="hangar_group_list",
        rate_limit_key=key_global,
        check_rate_limit=lambda key: check_rate_limit("hangar_group_list"),
        validate=None,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=tool_error_hook,
    )
    def hangar_group_list() -> dict:
        """List all provider groups with per-member details.

        Use this instead of hangar_list when you need member-level information:
        rotation status, weights, and individual member states.

        hangar_list returns group summaries (healthy_count, total_count).
        hangar_group_list returns full member breakdown.

        Returns:
            - groups: List of groups with member details

        Example:
            hangar_group_list()
            # Returns:
            # {
            #   "groups": [{
            #     "group_id": "llm-group",
            #     "state": "ready",
            #     "strategy": "round_robin",
            #     "healthy_count": 2,
            #     "total_count": 3,
            #     "members": [
            #       {"id": "llm-1", "state": "ready", "in_rotation": true, "weight": 1},
            #       {"id": "llm-2", "state": "ready", "in_rotation": true, "weight": 2},
            #       {"id": "llm-3", "state": "degraded", "in_rotation": false, "weight": 1}
            #     ]
            #   }]
            # }
        """
        ctx = get_context()
        return {"groups": [group.to_status_dict() for group in ctx.groups.values()]}

    @mcp.tool(name="hangar_group_rebalance")
    @mcp_tool_wrapper(
        tool_name="hangar_group_rebalance",
        rate_limit_key=lambda group: f"hangar_group_rebalance:{group}",
        check_rate_limit=check_rate_limit,
        validate=validate_provider_id_input,
        error_mapper=lambda exc: tool_error_mapper(exc),
        on_error=lambda exc, ctx_dict: tool_error_hook(exc, ctx_dict),
    )
    def hangar_group_rebalance(group: str) -> dict:
        """Force rebalancing for a group.

        Re-checks all members and updates rotation: recovered members rejoin,
        failed members are removed. Rebalancing happens automatically on
        health failures, but use this after manual intervention.

        Args:
            group: Group ID to rebalance.

        Returns:
            Returns an error if group ID is unknown.

        Example:
            hangar_group_rebalance("llm-group")
            # Returns:
            # {
            #   "group_id": "llm-group",
            #   "state": "ready",
            #   "healthy_count": 2,
            #   "total_members": 3,
            #   "members_in_rotation": ["llm-1", "llm-2"]
            # }
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
