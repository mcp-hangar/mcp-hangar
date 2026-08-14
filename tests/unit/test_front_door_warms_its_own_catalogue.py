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

from unittest.mock import patch

import pytest

from mcp_hangar.application.commands import StartMcpServerCommand
from mcp_hangar.domain.services.tool_access_resolver import (
    ToolAccessResolver,
    reset_tool_access_resolver,
)
from mcp_hangar.server.lifecycle import warm_the_front_door_catalogue

_SERVERS = ["payments", "everything"]


class _Repository:
    def __init__(self, ids: list[str]) -> None:
        self._ids = ids

    def get_all_ids(self) -> list[str]:
        return list(self._ids)


class _CommandBus:
    def __init__(self, fails: set[str] | None = None) -> None:
        self.sent: list[str] = []
        self._fails = fails or set()

    def send(self, command: StartMcpServerCommand) -> None:
        mcp_server_id = command.mcp_server_id
        self.sent.append(mcp_server_id)
        if mcp_server_id in self._fails:
            raise RuntimeError("upstream refused the connection")


class _Runtime:
    def __init__(self, repository: _Repository, command_bus: _CommandBus) -> None:
        self.repository = repository
        self.command_bus = command_bus


class _Context:
    def __init__(self, runtime: _Runtime) -> None:
        self.runtime = runtime


def _context(ids: list[str] | None = None, fails: set[str] | None = None) -> tuple[_Context, _CommandBus]:
    bus = _CommandBus(fails)
    return _Context(_Runtime(_Repository(_SERVERS if ids is None else ids), bus)), bus


@pytest.fixture(autouse=True)
def _clean_resolver():
    reset_tool_access_resolver()
    yield
    reset_tool_access_resolver()


def _in_mode(mode: str):
    resolver = ToolAccessResolver()
    resolver.set_topology_mode(mode)
    return patch(
        "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
        return_value=resolver,
    )


class TestFrontDoor:
    def test_every_configured_server_is_started(self) -> None:
        # The fix, in one assertion: the catalogue is a property of the
        # configuration, not of which replica the load balancer sent a start to.
        context, bus = _context()

        with _in_mode("front_door"):
            result = warm_the_front_door_catalogue(context)

        assert bus.sent == _SERVERS
        assert result == {"warmed": _SERVERS, "failed": []}

    def test_it_goes_through_the_command_bus(self) -> None:
        # Not `ensure_ready()`. The aggregate only RECORDS McpServerStarted; the
        # command handler is what drains and publishes it, and the publish is
        # what builds the projection. Called directly this would start the fleet
        # and leave `tools/list` empty until the GC worker's next sweep.
        context, bus = _context(ids=["payments"])
        sent: list[object] = []
        bus.send = sent.append  # type: ignore[method-assign]

        with _in_mode("front_door"):
            warm_the_front_door_catalogue(context)

        assert [type(command) for command in sent] == [StartMcpServerCommand]

    def test_one_dead_backend_does_not_cost_the_others_their_projection(self) -> None:
        # Fault barrier. A backend that is down at boot is reported and skipped;
        # the alternative is a single unreachable upstream leaving the whole
        # front door empty, which is the bug being fixed.
        context, bus = _context(fails={"payments"})

        with _in_mode("front_door"):
            result = warm_the_front_door_catalogue(context)

        assert bus.sent == _SERVERS
        assert result == {"warmed": ["everything"], "failed": ["payments"]}


class TestEgress:
    def test_nothing_is_started(self) -> None:
        # The default mode, and it must not change: `hangar_*` is the surface
        # there, lazy start on first use is what `idle_ttl_s` is designed around,
        # and starting every backend at boot changes what every existing
        # deployment costs to run.
        context, bus = _context()

        with _in_mode("egress"):
            result = warm_the_front_door_catalogue(context)

        assert bus.sent == []
        assert result == {"warmed": [], "failed": []}


class TestAnUnresolvableTopology:
    def test_it_warms_nothing_and_does_not_raise(self) -> None:
        # Boot must survive it. Warming nothing is the pre-existing behaviour,
        # so failing this way costs an empty catalogue and not the process.
        context, bus = _context()

        with patch(
            "mcp_hangar.domain.services.tool_access_resolver.get_tool_access_resolver",
            side_effect=RuntimeError("no resolver"),
        ):
            result = warm_the_front_door_catalogue(context)

        assert bus.sent == []
        assert result == {"warmed": [], "failed": []}
