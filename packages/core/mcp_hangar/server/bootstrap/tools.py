"""MCP tools registration."""

from mcp.server.fastmcp import FastMCP

from ...logging_config import get_logger
from ..tools import (
    register_batch_tools,
    register_discovery_tools,
    register_group_tools,
    register_hangar_tools,
    register_health_tools,
    register_load_tools,
    register_provider_tools,
)
from ..tools.continuation import register_continuation_tools

logger = get_logger(__name__)


def register_all_tools(mcp_server: FastMCP) -> None:
    """Register all MCP tools on the server.

    Args:
        mcp_server: FastMCP server instance.
    """
    register_hangar_tools(mcp_server)
    register_load_tools(mcp_server)
    register_provider_tools(mcp_server)
    register_health_tools(mcp_server)
    register_discovery_tools(mcp_server)
    register_group_tools(mcp_server)
    register_batch_tools(mcp_server)
    register_continuation_tools(mcp_server)
    logger.info("mcp_tools_registered")
