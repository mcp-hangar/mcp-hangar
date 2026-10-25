"""Start, on a front door, the servers a reload added or rebuilt, as boot starts every configured one.

In ``front_door`` the flat projection is ``tools/list``, and a server's tools
reach it only from ``McpServerStarted``. Boot starts every configured server for
that reason (`lifecycle.warm_the_front_door_catalogue`). A reload committed the
servers it added, and the ones it rebuilt, cold and started nothing, so an added
server's tools were never listed, no ``notifications/tools/list_changed`` was
sent, and a call to one answered ``-32601`` until a restart. A rebuilt server
kept the projection of the process the reload stopped. Nothing else could fix
it: the meta-API, ``hangar_start`` among it, is not projected for an ordinary
tenant.

This starts exactly those servers, the way boot does: through the command bus,
so ``McpServerStarted`` is published and the projection handler runs; one at a
time, a failure logged by its type and not costing the others their start; a
server restored ``dead`` skipped. The projection change then reaches every
client through the registry's own listener (`fastmcp_server.tool_list_changed`),
flushed at the end as boot's warm-up flushes.

**Only in ``front_door``.** In ``egress`` a server starts on first use, which is
what ``idle_ttl_s`` is designed around, and a reload starts nothing, as before.

On a thread of its own, after the reload has committed: a backend handshake is
I/O, and the reload, its response and its lock must not wait on it.
``ConfigurationReloaded`` is published once the servers, their policies and pins
are in force, so no tool is projected before what governs it.

``LOCAL_VIEW``: it acts on this replica's fleet, and each replica reloads its own
file. Starting a server publishes ``McpServerStarted``, which a ``PROJECTION``
handler may not do.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from ...domain.contracts.event_bus import HandlerKind
from ...domain.events import ConfigurationReloaded
from ...errors import bounded_error_type
from ...logging_config import get_logger

if TYPE_CHECKING:
    from ...bootstrap.runtime import Runtime

logger = get_logger(__name__)


class ReloadWarmUp:
    """Starts, on a front door, what a reload added or rebuilt."""

    def __init__(self, runtime: Runtime) -> None:
        self._runtime = runtime

    def handle(self, event: object) -> None:
        """On ``ConfigurationReloaded`` in ``front_door``, start its added and updated servers on a thread."""
        from ...domain.services.tool_access_resolver import is_front_door

        if not isinstance(event, ConfigurationReloaded) or not is_front_door():
            return
        mcp_server_ids = [*event.mcp_servers_added, *event.mcp_servers_updated]
        if not mcp_server_ids:
            return
        threading.Thread(target=self.warm, args=(mcp_server_ids,), name="mcp-hangar-reload-warmup", daemon=True).start()

    def warm(self, mcp_server_ids: list[str]) -> None:
        """Start each of *mcp_server_ids* still in the fleet, then tell the clients at once."""
        from ...application.commands import StartMcpServerCommand
        from ...fastmcp_server import tool_list_changed

        warmed = failed = skipped = 0
        try:
            for mcp_server_id in mcp_server_ids:
                server = self._runtime.repository.get(mcp_server_id)
                # Gone (a later reload removed it, or a group id that is not a
                # server), or restored dead: boot does not start either (#1361).
                if server is None or server.state.value == "dead":
                    skipped += 1
                    continue
                try:
                    self._runtime.command_bus.send(StartMcpServerCommand(mcp_server_id=mcp_server_id))
                    warmed += 1
                except Exception as e:  # noqa: BLE001 -- fault-barrier: one dead backend must not cost the others their projection
                    failed += 1
                    # The type only: a start failure's text can carry what the upstream printed.
                    logger.warning(
                        "front_door_reload_warmup_failed",
                        mcp_server_id=mcp_server_id,
                        error_type=bounded_error_type(type(e).__qualname__),
                    )
        finally:
            tool_list_changed.flush_now()
        logger.info("front_door_reload_warmup_complete", warmed=warmed, failed=failed, skipped=skipped)


def subscribe_reload_warm_up(runtime: Runtime) -> ReloadWarmUp:
    """Wire `ReloadWarmUp` to ``ConfigurationReloaded`` and return it."""
    warm_up = ReloadWarmUp(runtime)
    runtime.event_bus.subscribe(ConfigurationReloaded, warm_up.handle, kind=HandlerKind.LOCAL_VIEW)
    return warm_up
