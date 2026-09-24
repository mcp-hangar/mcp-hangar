"""Populate the ToolProjectionRegistry from tool discovery.

Subscribes to :class:`McpServerStarted`. When a backend mcp_server finishes
starting, its tools have already been populated on the aggregate (config
predefined tools at init, plus handshake-discovered tools — both before the
event fires). This handler reads those tool schemas from the aggregate and
feeds them to :meth:`ToolProjectionRegistry.build_from_tools`, so the registry
holds real projections (schema + digest) for per-tenant projection (#232) and
so withdrawal overlays (#244/#235) compose against actual tools.

It also runs when an upstream refreshes its catalogue after start on its own
``notifications/tools/list_changed`` (#1366): the aggregate announces that on
:mod:`~mcp_hangar.domain.services.tool_catalogue_changes`, and the same
:meth:`ToolProjectionPopulationHandler.project` rebuilds that server's
projections. Without it the front door told a client to re-list and served it
the catalogue from the last start. ``subscribe_tool_projection`` in
``server/bootstrap/event_handlers.py`` wires both triggers, so bootstrap and a
test harness cannot wire only one.

The lazy tool refresh in ``McpServer.invoke_tool`` (a call for a tool the
catalogue does not hold) announces through the same seam, so what it finds is
projected at once, and the registry's change listeners tell the front door's
clients (``fastmcp_server/tool_list_changed.py``).

**Reads local state, not the event**, which is why the subscription is
``HandlerKind.LOCAL_VIEW`` -- see that member for what running it on a peer's
event cost (#922).
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
        self.project(event.mcp_server_id)

    def project(self, mcp_server_id: str) -> None:
        """Replace *mcp_server_id*'s projections with the tools its aggregate holds now.

        The one path into the registry for discovered tools: a start, and an
        upstream's own ``tools/list_changed`` after start (#1366).
        """
        server = self._repository.get(mcp_server_id)
        if server is None:
            return

        catalog = getattr(server, "tools", None)
        if catalog is None:
            return

        # ToolCatalog.list_tools() -> list[ToolSchema]; build_from_tools replaces
        # this server's projections atomically (safe to call on every start).
        # Other servers' projections, and every withdrawal and pin overlay, are
        # keyed apart and left alone.
        tools = catalog.list_tools()
        get_tool_projection_registry().build_from_tools(mcp_server_id, tools)
        logger.debug(
            "tool_projection_populated_from_discovery",
            mcp_server_id=mcp_server_id,
            tool_count=len(tools),
        )
