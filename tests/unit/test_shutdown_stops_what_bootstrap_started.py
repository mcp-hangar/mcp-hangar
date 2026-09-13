"""Shutdown stops what bootstrap started (#1389).

`SagaManager.cancel_all_scheduled_commands` existed and nothing called it, so a
retry a saga had scheduled could fire while the process went down. The loops
behind the fleet writer and the fleet projection were threads nothing stopped.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.infrastructure.async_bridge import BackgroundLoop
from mcp_hangar.infrastructure.saga_manager import SagaManager
from mcp_hangar.server.bootstrap import ApplicationContext
from mcp_hangar.server.bootstrap.cqrs import _fleet_writer
from mcp_hangar.server.lifecycle import ServerLifecycle


def _context(**fields: Any) -> ApplicationContext:
    return ApplicationContext(runtime=MagicMock(), mcp_server=MagicMock(), **fields)


class _Repository:
    async def save(self, snapshot: McpServerConfigSnapshot) -> None:
        pass


def test_shutdown_cancels_a_scheduled_command_before_it_is_sent() -> None:
    bus = MagicMock()
    manager = SagaManager(command_bus=bus, event_bus=MagicMock())
    manager.schedule_command(StartMcpServerCommand(mcp_server_id="math"), delay_s=0.2)
    timers = list(manager._pending_timers.values())

    ServerLifecycle(_context(saga_manager=manager)).shutdown()

    for timer in timers:
        timer.join(1.0)  # long enough for one that was not cancelled to fire
        assert not timer.is_alive()
    bus.send.assert_not_called()


def test_shutdown_stops_the_fleet_writer_bootstrap_built() -> None:
    writer = _fleet_writer(SimpleNamespace(config_repository=_Repository()))
    writer.save(McpServerConfigSnapshot(mcp_server_id="math", mode="subprocess"))
    thread = writer._loop._thread
    assert thread is not None and thread.is_alive()

    _context().shutdown()

    # The writer is still referenced here, so this is shutdown's doing and not
    # the finalizer's.
    assert not thread.is_alive()


def test_a_loop_dropped_without_close_stops_its_thread() -> None:
    loop = BackgroundLoop()
    loop.run(asyncio.sleep(0), 5.0)
    thread = loop._thread
    assert thread is not None

    del loop

    thread.join(BackgroundLoop.JOIN_TIMEOUT_S)
    assert not thread.is_alive()
