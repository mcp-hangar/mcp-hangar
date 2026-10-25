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

import threading
from typing import TYPE_CHECKING, Any

from ...application.read_models.tool_projection import get_tool_projection_registry
from ...domain.events import (
    ConfigurationReloaded,
    McpServerDeregistered,
    McpServerHotUnloaded,
    McpServerRegistered,
    McpServerStarted,
    ToolAdmissionRequested,
)
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...domain.repository import IMcpServerRepository

logger = get_logger(__name__)


class ToolProjectionPopulationHandler:
    """Populates the ToolProjectionRegistry when a mcp_server starts."""

    def __init__(self, repository: IMcpServerRepository, event_bus: Any = None) -> None:
        """Initialize with the mcp_server repository.

        Args:
            repository: Repository used to fetch the started mcp_server and
                read its discovered tool schemas.
            event_bus: Where a request to admit a withheld tool is published
                (#1801). None: the tool is withheld and nothing is announced.
        """
        self._repository = repository
        self._event_bus = event_bus
        #: (server, tool, digest) already announced, so a re-listing asks once.
        self._requested: set[tuple[str, str, str]] = set()
        self._requested_lock = threading.Lock()

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
        registry = get_tool_projection_registry()
        registry.build_from_tools(mcp_server_id, tools)
        # A delete between the read above and the build would otherwise have
        # its forget undone by this write (#2088).
        if self._repository.get(mcp_server_id) is None:
            self.forget(mcp_server_id)
            return
        self._request_admission(registry, mcp_server_id)
        logger.debug(
            "tool_projection_populated_from_discovery",
            mcp_server_id=mcp_server_id,
            tool_count=len(tools),
        )

    def forget(self, mcp_server_id: str) -> None:
        """Drop *mcp_server_id*'s projections: it left the fleet (#2088).

        The registry tells its change listeners, which is how a listing client
        hears ``tools/list_changed``; nothing is published on the event bus. The
        registry keeps the entries aside, for :meth:`restore` to put back if the
        server is registered again. Its pending admission requests are forgotten
        too, so a withheld tool of a server added back under the same name asks
        again.
        """
        with self._requested_lock:
            self._requested.difference_update({key for key in self._requested if key[0] == mcp_server_id})
        if get_tool_projection_registry().forget_server(mcp_server_id):
            logger.info("tool_projection_dropped_for_removed_server", mcp_server_id=mcp_server_id)

    def handle_deregistered(self, event: object) -> None:
        """On McpServerDeregistered, drop the server's projections, on every replica.

        A REST or discovery delete. Reads only the event's server id, so it may
        run on a peer's tailed event, where `FleetProjection` removes the server
        from the local fleet on the same event.
        """
        if isinstance(event, McpServerDeregistered):
            self.forget(event.mcp_server_id)

    def restore(self, mcp_server_id: str) -> None:
        """Put back the entries *mcp_server_id* had when it was removed, if it has none now.

        A server registered again under a removed name is often not started by
        this replica: a peer's create arrives as ``McpServerRegistered`` while
        the start stays on the peer, and a discovery or operator re-create starts
        nothing. Listed with its last known tools, the first flat call routes to
        the new copy and starts it -- what a re-created server did before #2088,
        when nothing was removed at all. A local start replaces the entries.
        """
        if get_tool_projection_registry().restore_server(mcp_server_id):
            logger.info("tool_projection_restored_for_registered_server", mcp_server_id=mcp_server_id)

    def handle_registered(self, event: object) -> None:
        """On McpServerRegistered, restore a removed server's last entries, on every replica.

        The counterpart of :meth:`handle_deregistered`, and the same kind. Only
        for a server the local fleet holds: on a peer, `FleetProjection` may
        decline the new copy, and its old tools must not be listed then.
        """
        if isinstance(event, McpServerRegistered) and self._repository.exists(event.mcp_server_id):
            self.restore(event.mcp_server_id)

    def handle_left_locally(self, event: object) -> None:
        """On a reload or a hot unload, drop what left this replica's fleet, and restore what came back.

        A removal drops only a server the local repository no longer holds: a
        hot-loaded server never was in it, and a declared server by the same
        name must keep its tools. A reload's rebuilt servers are not here; their
        start replaces their projections. A server a reload adds under a removed
        name gets its last entries back until its warm-up start, or a call,
        replaces them.
        """
        if isinstance(event, ConfigurationReloaded):
            removed = list(event.mcp_servers_removed)
            for mcp_server_id in event.mcp_servers_added:
                if self._repository.exists(mcp_server_id):
                    self.restore(mcp_server_id)
        elif isinstance(event, McpServerHotUnloaded):
            removed = [event.mcp_server_id]
        else:
            return
        for mcp_server_id in removed:
            if not self._repository.exists(mcp_server_id):
                self.forget(mcp_server_id)

    def _request_admission(self, registry: Any, mcp_server_id: str) -> None:
        """Publish ``ToolAdmissionRequested`` once for each tool the server withholds (#1801)."""
        if self._event_bus is None or registry.new_tools_policy(mcp_server_id) != "withhold":
            return
        for projection in registry.list_for_server(mcp_server_id):
            if not registry.is_unadmitted(mcp_server_id, projection.tool):
                continue
            key = (mcp_server_id, projection.tool, projection.digest.sha256)
            with self._requested_lock:
                if key in self._requested:
                    continue
                self._requested.add(key)
            logger.warning(
                "tool_withheld_until_admitted",
                mcp_server_id=mcp_server_id,
                tool=projection.tool,
                observed_digest=projection.digest.sha256,
            )
            self._event_bus.publish(
                ToolAdmissionRequested(
                    mcp_server_id=mcp_server_id, tool_name=projection.tool, observed_digest=projection.digest.sha256
                )
            )
