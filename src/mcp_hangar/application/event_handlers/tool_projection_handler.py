"""Populate the ToolProjectionRegistry from tool discovery.

Subscribes to :class:`McpServerStarted`. When a backend mcp_server finishes
starting, its tools have already been populated on the aggregate (config
predefined tools at init, plus handshake-discovered tools — both before the
event fires). This handler reads those tool schemas from the aggregate and
feeds them to :meth:`ToolProjectionRegistry.build_from_tools`, so the registry
holds real projections (schema + digest) for per-tenant projection (#232) and
so withdrawal overlays (#244/#235) compose against actual tools.

Known gap (follow-up): the lazy tool refresh in ``McpServer.invoke_tool``
(tools added after start) fires no event, so the registry is not repopulated
until the next start. Initial population is the goal here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ...application.read_models.tool_projection import get_tool_projection_registry
from ...domain.events import McpServerStarted
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...domain.repository import IMcpServerRepository

logger = get_logger(__name__)


class ToolProjectionPopulationHandler:
    """Populates the ToolProjectionRegistry when a mcp_server starts."""

    def __init__(self, repository: IMcpServerRepository) -> None:
        """Initialize with the mcp_server repository.

        Args:
            repository: Repository used to fetch the started mcp_server and
                read its discovered tool schemas.
        """
        self._repository = repository

    def handle(self, event: object) -> None:
        """On McpServerStarted, populate the registry from the server's tools."""
        if not isinstance(event, McpServerStarted):
            return

        mcp_server_id = event.mcp_server_id
        server = self._repository.get(mcp_server_id)
        if server is None:
            return

        catalog = getattr(server, "tools", None)
        if catalog is None:
            return

        # ToolCatalog.list_tools() -> list[ToolSchema]; build_from_tools replaces
        # this server's projections atomically (safe to call on every start).
        tools = catalog.list_tools()
        get_tool_projection_registry().build_from_tools(mcp_server_id, tools)
        logger.debug(
            "tool_projection_populated_from_discovery",
            mcp_server_id=mcp_server_id,
            tool_count=len(tools),
        )
