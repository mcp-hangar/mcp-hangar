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

        CHOOSE THIS when: you need member-level details (rotation, weights, individual states).
        CHOOSE hangar_list when: you need group summaries with provider list.
        CHOOSE hangar_details when: you need full info for a specific group.

        Side effects: None (read-only).

        Args:
            None

        Returns:
            {
                groups: [{
                    group_id: str,
                    description: str,
                    state: str,
                    strategy: str,
                    min_healthy: int,
                    healthy_count: int,
                    total_members: int,
                    is_available: bool,
                    circuit_open: bool,
                    members: [{id, state, in_rotation, weight, priority, consecutive_failures}]
                }]
            }

        Example:
            hangar_group_list()
            # {"groups": [{"group_id": "llm-group", "state": "ready", "strategy": "round_robin",
            #   "healthy_count": 2, "total_members": 3, "members": [
            #     {"id": "llm-1", "state": "ready", "in_rotation": true, "weight": 1},
            #     {"id": "llm-2", "state": "ready", "in_rotation": true, "weight": 1},
            #     {"id": "llm-3", "state": "degraded", "in_rotation": false, "weight": 1}
            #   ]}]}

            hangar_group_list()  # when no groups configured
            # {"groups": []}
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
        """Force rebalancing for a provider group.

        CHOOSE THIS when: after manual intervention, or to recover members faster than auto.
        CHOOSE hangar_start when: starting all group members from cold state.
        SKIP THIS for normal operation - rebalancing happens automatically on health failures.

        Side effects: Re-checks all members. Recovered members rejoin rotation, failed removed.

        Args:
            group: str - Group ID

        Returns:
            {
                group_id: str,
                state: str,
                healthy_count: int,
                total_members: int,
                members_in_rotation: list[str]
            }
            Error: ValueError with "unknown_group: <id>"

        Example:
            hangar_group_rebalance("llm-group")
            # {"group_id": "llm-group", "state": "ready", "healthy_count": 2,
            #  "total_members": 3, "members_in_rotation": ["llm-1", "llm-2"]}

            hangar_group_rebalance("unknown")
            # Error: unknown_group: unknown
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
