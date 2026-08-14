"""A peer restarting a server must not remove tools this replica is serving.

The assertion `tests/unit/test_handlers_say_where_they_may_run.py` could not
make. That file pins the *classification* by reading the subscription line as
text; it never drives a tailed event and never looks at the registry. So the
handler read as covered for two releases while the behaviour it implies had
never once been exercised -- and the behaviour was data loss (#922).

What the handler actually does with `McpServerStarted` is re-read the **local**
aggregate: the event carries `tools_count`, not the schemas. Delivered a peer's
start, it answered with what this replica knew about that server, and
`build_from_tools` applies that answer as a *replace*. A follower whose own copy
of the server was cold therefore rebuilt it from nothing and deleted the seven
tools it had been correctly serving, on a peer's routine restart, having been
asked for nothing.

Measured on the cluster before the fix (2.6.0, two replicas, one token):

    both replicas warmed              P1=32  P2=32
    stop+start `payments` on P1 only  P1=32  P2=25   <- P2 lost payments' tools

The cold/warm split matters and is why the first attempt at a live repro did
*not* reproduce: with the follower warm, the rebuild reads real tools and is a
genuine no-op. Both cases are covered here so the fix cannot be mistaken for the
accident that used to hide the bug.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.event_handlers.tool_projection_handler import (
    ToolProjectionPopulationHandler,
)
from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import McpServerStarted
from mcp_hangar.domain.model.tool_catalog import ToolSchema
from mcp_hangar.infrastructure.event_bus import EventBus

_SERVER = "payments"
_TOOLS = ("charge", "refund")


def _schema(name: str) -> ToolSchema:
    return ToolSchema(
        name=name,
        description=f"Does {name}",
        input_schema={"type": "object", "properties": {}},
    )


class _Catalog:
    def __init__(self, tools: tuple[str, ...]) -> None:
        self._tools = [_schema(name) for name in tools]

    def list_tools(self) -> list[ToolSchema]:
        return list(self._tools)


class _Server:
    """Just enough of the aggregate for the handler: a tool catalogue."""

    def __init__(self, tools: tuple[str, ...]) -> None:
        self.tools = _Catalog(tools)


class _Repository:
    def __init__(self, servers: dict[str, _Server]) -> None:
        self._servers = servers

    def get(self, mcp_server_id: str) -> _Server | None:
        return self._servers.get(mcp_server_id)


def _started() -> McpServerStarted:
    return McpServerStarted(
        mcp_server_id=_SERVER,
        mode="remote",
        tools_count=len(_TOOLS),
        startup_duration_ms=1.0,
    )


def _tool_names() -> set[str]:
    return {projection.tool for projection in get_tool_projection_registry().list_for_server(_SERVER)}


@pytest.fixture(autouse=True)
def _clean_registry():
    reset_tool_projection_registry()
    yield
    reset_tool_projection_registry()


def _bus_for(repository: _Repository) -> EventBus:
    bus = EventBus()
    handler = ToolProjectionPopulationHandler(repository=repository)
    # The production kind, from `server/bootstrap/event_handlers.py`. The
    # companion test asserts the wiring still says this; this one asserts what
    # saying it buys.
    bus.subscribe(McpServerStarted, handler.handle, kind=HandlerKind.LOCAL_VIEW)
    return bus


class TestAPeersStart:
    def test_a_cold_follower_keeps_the_catalogue_it_was_serving(self) -> None:
        # The reported defect, exactly: projection non-empty, local copy cold.
        # Before the fix this asserted set went empty and a tenant listing tools
        # against this replica stopped seeing `payments` at all.
        get_tool_projection_registry().build_from_tools(_SERVER, [_schema(t) for t in _TOOLS])
        bus = _bus_for(_Repository({_SERVER: _Server(())}))

        bus.deliver_tailed(_started())

        assert _tool_names() == set(_TOOLS)

    def test_a_warm_follower_is_unchanged_too(self) -> None:
        # The case that hid the bug. It passed before the fix and must keep
        # passing after it, or the fix is really "the handler stopped working".
        get_tool_projection_registry().build_from_tools(_SERVER, [_schema(t) for t in _TOOLS])
        bus = _bus_for(_Repository({_SERVER: _Server(_TOOLS)}))

        bus.deliver_tailed(_started())

        assert _tool_names() == set(_TOOLS)

    def test_a_follower_that_never_knew_the_server_gains_nothing(self) -> None:
        # Documents the limit rather than the fix. This is #886's direction, and
        # it is NOT closed here: `McpServerStarted` carries no schemas, so there
        # is nothing to populate from. Every replica ends up with the whole
        # catalogue by starting the servers itself (#885), not by tailing.
        bus = _bus_for(_Repository({_SERVER: _Server(())}))

        bus.deliver_tailed(_started())

        assert _tool_names() == set()


class TestOurOwnStart:
    def test_it_still_populates_the_registry(self) -> None:
        # The handler's whole job (#248). A fix that filtered the local event
        # too would leave `front_door` serving an empty list forever.
        bus = _bus_for(_Repository({_SERVER: _Server(_TOOLS)}))

        bus.publish(_started())

        assert _tool_names() == set(_TOOLS)

    def test_it_still_replaces_rather_than_merges(self) -> None:
        # Replace is correct for our own server: a tool the upstream dropped has
        # to leave the catalogue. Only the *foreign* rebuild was wrong.
        get_tool_projection_registry().build_from_tools(_SERVER, [_schema("charge"), _schema("chargeback")])
        bus = _bus_for(_Repository({_SERVER: _Server(("charge",))}))

        bus.publish(_started())

        assert _tool_names() == {"charge"}
