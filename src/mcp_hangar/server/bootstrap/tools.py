"""MCP tools registration."""

from mcp_hangar._sdk_compat import FastMCP

from ...logging_config import get_logger
from ..tools import (
    register_batch_tools,
    register_discovery_tools,
    register_group_tools,
    register_hangar_tools,
    register_health_tools,
    register_load_tools,
    register_mcp_server_tools,
)
from ..tools.continuation import register_continuation_tools

logger = get_logger(__name__)


def register_all_tools(mcp_server: FastMCP) -> None:
    """Register all MCP tools and custom HTTP routes on the server.

    Args:
        mcp_server: FastMCP server instance.
    """
    # Before the tools, so no registration path can produce a tool that is
    # reachable without passing the table. The hook is injected because the
    # table and the auth components live here in delivery and `mcp_tool_wrapper`
    # lives in application; the layering does not let it reach them (#909).
    from ...application.mcp.tooling import set_tool_authorizer
    from ..tools.tool_permissions import authorize_tool

    set_tool_authorizer(authorize_tool)

    register_hangar_tools(mcp_server)
    register_load_tools(mcp_server)
    register_mcp_server_tools(mcp_server)
    register_health_tools(mcp_server)
    register_discovery_tools(mcp_server)
    register_group_tools(mcp_server)
    register_batch_tools(mcp_server)
    register_continuation_tools(mcp_server)

    from ...fastmcp_server.interceptors_list import register_interceptors_list

    register_interceptors_list(mcp_server)

    logger.info("mcp_tools_registered")
