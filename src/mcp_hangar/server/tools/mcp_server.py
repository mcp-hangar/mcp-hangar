"""McpServer interaction tools: hangar_tools, hangar_details, hangar_warm.

Uses ApplicationContext for dependency injection (DIP).
Separates commands (write) from queries (read) following CQRS.

Note: Tool invocation is handled by hangar_call in batch/.
"""

import logging
from typing import Any

from mcp.server.fastmcp import FastMCP

from ...application.commands import StartMcpServerCommand
from ...application.mcp.tooling import mcp_tool_wrapper
from ...application.queries import GetMcpServerQuery, GetMcpServerToolsQuery
from ...domain.services import get_tool_access_resolver
from ...metrics import TOOLS_FILTERED_TOTAL
from ..context import get_context
from ..validation import check_rate_limit, tool_error_hook, tool_error_mapper, validate_mcp_server_id_input

logger = logging.getLogger(__name__)

# =============================================================================
# Helper Functions
# =============================================================================


def _get_tools_for_group(mcp_server: str) -> dict[str, Any]:
    """Get tools for a mcp_server group."""
    ctx = get_context()
    group = ctx.get_group(mcp_server)
    selected = group.select_member()

    if not selected:
        raise ValueError(f"no_healthy_members_in_group: {mcp_server}")

    ctx.command_bus.send(StartMcpServerCommand(mcp_server_id=selected.mcp_server_id))
    query = GetMcpServerToolsQuery(mcp_server_id=selected.mcp_server_id)
    tools = ctx.query_bus.execute(query)

    # Apply tool access filtering for group context
    resolver = get_tool_access_resolver()
    filtered_tools = resolver.filter_tools(
        mcp_server_id=selected.mcp_server_id,
        tools=tools,
        group_id=mcp_server,
        member_id=selected.mcp_server_id,
    )

    if len(filtered_tools) < len(tools):
        filtered_count = len(tools) - len(filtered_tools)
        TOOLS_FILTERED_TOTAL.set(filtered_count, mcp_server=mcp_server)
        logger.debug(
            "tools_filtered_by_policy: mcp_server_id=%s, group_id=%s, total=%d, visible=%d, filtered=%d",
            selected.mcp_server_id,
            mcp_server,
            len(tools),
            len(filtered_tools),
            filtered_count,
        )

    return {
        "mcp_server": mcp_server,
        "group": True,
        "tools": [t.to_dict() for t in filtered_tools],
    }


def _get_tools_for_mcp_server(mcp_server: str) -> dict[str, Any]:
    """Get tools for a single mcp_server."""
    ctx = get_context()
    mcp_server_obj = ctx.get_mcp_server(mcp_server)
    resolver = get_tool_access_resolver()

    # If mcp_server has predefined tools, return them without starting
    if mcp_server_obj.has_tools:
        tools = mcp_server_obj.tools.list_tools()
        # Apply tool access filtering
        filtered_tools = resolver.filter_tools(mcp_server_id=mcp_server, tools=tools)

        if len(filtered_tools) < len(tools):
            filtered_count = len(tools) - len(filtered_tools)
            TOOLS_FILTERED_TOTAL.set(filtered_count, mcp_server=mcp_server)
            logger.debug(
                "tools_filtered_by_policy: mcp_server_id=%s, total=%d, visible=%d, filtered=%d",
                mcp_server,
                len(tools),
                len(filtered_tools),
                filtered_count,
            )

        return {
            "mcp_server": mcp_server,
            "state": mcp_server_obj.state.value,
            "predefined": mcp_server_obj.tools_predefined,
            "tools": [t.to_dict() for t in filtered_tools],
        }

    # Start mcp_server and discover tools
    ctx.command_bus.send(StartMcpServerCommand(mcp_server_id=mcp_server))
    query = GetMcpServerToolsQuery(mcp_server_id=mcp_server)
    tools = ctx.query_bus.execute(query)

    # Apply tool access filtering
    filtered_tools = resolver.filter_tools(mcp_server_id=mcp_server, tools=tools)

    if len(filtered_tools) < len(tools):
        filtered_count = len(tools) - len(filtered_tools)
        TOOLS_FILTERED_TOTAL.set(filtered_count, mcp_server=mcp_server)
        logger.debug(
            "tools_filtered_by_policy: mcp_server_id=%s, total=%d, visible=%d, filtered=%d",
            mcp_server,
            len(tools),
            len(filtered_tools),
            filtered_count,
        )

    return {
        "mcp_server": mcp_server,
        "state": mcp_server_obj.state.value,
        "predefined": False,
        "tools": [t.to_dict() for t in filtered_tools],
    }


# =============================================================================
# Tool Registration
# =============================================================================


def register_mcp_server_tools(mcp: FastMCP) -> None:
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
        rate_limit_key=lambda mcp_server: f"hangar_tools:{mcp_server}",
        check_rate_limit=check_rate_limit,
        validate=validate_mcp_server_id_input,
        error_mapper=tool_error_mapper,
        on_error=lambda exc, ctx: tool_error_hook(exc, ctx),
    )
    def hangar_tools(mcp_server: str) -> dict:
        """Get tool schemas (JSON Schema) for a mcp_server.

        CHOOSE THIS when: you need tool names and input schemas before calling.
        CHOOSE hangar_details when: you need mcp_server config, health, or runtime info.
        CHOOSE hangar_call when: you already know the tool name and want to invoke it.

        Side effects: May start a cold mcp_server to discover tools.

        Args:
            mcp_server: str - McpServer ID or Group ID

        Returns:
            McpServer: {
                mcp_server: str,
                state: str,
                predefined: bool,
                tools: [{name: str, description: str, inputSchema: object}]
            }
            Group: {
                mcp_server: str,
                group: true,
                tools: [{name: str, description: str, inputSchema: object}]
            }
            Error: ValueError with "unknown_mcp_server: <id>" or "no_healthy_members_in_group: <id>"

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
        check_rate_limit=check_rate_limit,
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
                mcp_server: str,
                state: str,
                mode: str,
                alive: bool,
                tools: [{name, description, inputSchema}],
                health: {consecutive_failures: int, last_check: str, ...},
                idle_time: float,
                meta: object
            }
            Group: {
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
            }
            Error: ValueError with "unknown_mcp_server: <id>"

        Example:
            hangar_details("math")
            # {"mcp_server": "math", "state": "ready", "mode": "subprocess",
            #  "alive": true, "tools": [...], "health": {"consecutive_failures": 0},
            #  "idle_time": 12.5, "meta": {}}

            hangar_details("llm-group")
            # {"group_id": "llm-group", "state": "ready", "strategy": "round_robin",
            #  "healthy_count": 2, "total_members": 3, "members": [...]}

            hangar_details("unknown")
            # Error: unknown_mcp_server: unknown
        """
        ctx = get_context()

        if ctx.group_exists(mcp_server):
            return ctx.get_group(mcp_server).to_status_dict()

        if not ctx.mcp_server_exists(mcp_server):
            raise ValueError(f"unknown_mcp_server: {mcp_server}")

        query = GetMcpServerQuery(mcp_server_id=mcp_server)
        result = ctx.query_bus.execute(query).to_dict()

        # Add tool access policy summary
        resolver = get_tool_access_resolver()
        result["tools_policy"] = resolver.get_policy_summary(mcp_server)

        # Filter tools in the response if present
        if "tools" in result and result["tools"]:
            original_count = len(result["tools"])
            result["tools"] = resolver.filter_tool_dicts(mcp_server, result["tools"])
            filtered_count = original_count - len(result["tools"])
            if filtered_count > 0:
                result["tools_policy"]["filtered_count"] = filtered_count

        return result

    @mcp.tool(name="hangar_warm")
    @mcp_tool_wrapper(
        tool_name="hangar_warm",
        rate_limit_key=lambda mcp_servers="": "hangar_warm",
        check_rate_limit=check_rate_limit,
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

        Args:
            mcp_servers: str - Comma-separated mcp_server IDs, or null to warm all

        Returns:
            {
                warmed: list[str],
                already_warm: list[str],
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
                if mcp_server_obj and mcp_server_obj.state.value == "ready":
                    already_warm.append(mcp_server_id)
                else:
                    command = StartMcpServerCommand(mcp_server_id=mcp_server_id)
                    ctx.command_bus.send(command)
                    warmed.append(mcp_server_id)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: single mcp_server warm failure must not crash batch
                failed.append({"id": mcp_server_id, "error": str(e)[:100]})

        return {
            "warmed": warmed,
            "already_warm": already_warm,
            "failed": failed,
            "summary": f"Warmed {len(warmed)} mcp_servers, {len(already_warm)} already warm, {len(failed)} failed",
        }
