"""A front-door replica warms itself, so `tools/list` stops being a warm-up log.

In `front_door` the flat projection IS `tools/list`, and it is built from
`McpServerStarted`. A replica that has started nothing has discovered nothing, so
after every restart it served `[]` to a valid tenant and no client could change
that: the meta-API is not projected for an ordinary tenant, a tool name the
client already knows resolves against the same empty map, and health checks skip
cold servers by construction. Two replicas that happened to warm different
servers then answered the same tenant differently -- 18 tools and 0, alternating
through the Service (#878, #885, #886).

What is asserted here is the decision, not the thread: which servers get a start
command, in which topology, and through which path. The thread is one line in
`ServerLifecycle.start` and asserting it would be asserting `threading`.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.domain.services.tool_access_resolver import (
    get_tool_access_resolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.lifecycle import warm_the_front_door_catalogue

_SERVERS = ["payments", "everything"]


class _CommandBus:
    """Records the commands it is sent; refuses the ones named in *fails*."""

    def __init__(self, fails: set[str]) -> None:
        self.sent: list[StartMcpServerCommand] = []
        self._fails = fails

    def send(self, command: StartMcpServerCommand) -> None:
        self.sent.append(command)
        if command.mcp_server_id in self._fails:
            raise RuntimeError("upstream refused the connection")


def _runtime(fails: set[str] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        repository=SimpleNamespace(get_all_ids=lambda: list(_SERVERS)),
        command_bus=_CommandBus(fails or set()),
    )


@pytest.fixture(autouse=True)
def _clean_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


def _in_mode(mode: str) -> None:
    get_tool_access_resolver().set_topology_mode(mode)


class TestFrontDoor:
    def test_every_configured_server_is_started(self) -> None:
        # The fix, in one assertion: the catalogue is a property of the
        # configuration, not of which replica the load balancer sent a start to.
        _in_mode("front_door")
        runtime = _runtime()

        warm_the_front_door_catalogue(runtime)

        assert [command.mcp_server_id for command in runtime.command_bus.sent] == _SERVERS

    def test_it_goes_through_the_command_bus(self) -> None:
        # Not `ensure_ready()`. The aggregate only RECORDS McpServerStarted; the
        # command handler is what drains and publishes it, and the publish is
        # what builds the projection. Called directly this would start the fleet
        # and leave `tools/list` empty until the GC worker's next sweep.
        _in_mode("front_door")
        runtime = _runtime()

        warm_the_front_door_catalogue(runtime)

        assert {type(command) for command in runtime.command_bus.sent} == {StartMcpServerCommand}

    def test_one_dead_backend_does_not_cost_the_others_their_projection(self) -> None:
        # Fault barrier. A backend that is down at boot is skipped and the sweep
        # carries on; the alternative is a single unreachable upstream leaving
        # the whole front door empty, which is the bug being fixed.
        _in_mode("front_door")
        runtime = _runtime(fails={"payments"})

        warm_the_front_door_catalogue(runtime)

        assert [command.mcp_server_id for command in runtime.command_bus.sent] == _SERVERS


class TestEgress:
    def test_nothing_is_started(self) -> None:
        # The default mode, and it must not change: `hangar_*` is the surface
        # there, lazy start on first use is what `idle_ttl_s` is designed around,
        # and starting every backend at boot changes what every existing
        # deployment costs to run.
        _in_mode("egress")
        runtime = _runtime()

        warm_the_front_door_catalogue(runtime)

        assert runtime.command_bus.sent == []
