"""Discovery is the door nobody opens by hand, and it kept no record.

Five event classes describe exactly what it does -- a server appeared, its
definition changed under us, it was refused, it went away -- and nothing
constructed a single one of them. The vocabulary shipped, the feature went live,
and the log stayed empty (#762). No test could see it: each half was correct on
its own, which is why the producer/consumer gate exists and why this file pins
the emitters rather than the classes.

Four of the five are emitted here. The two cycle-level ones are declared
unemitted on purpose in `[tool.event_contracts]`: at a 30s refresh a cycle event
is 2880 rows a day per gateway saying nothing changed.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.events import (
    McpServerDiscovered,
    McpServerDiscoveryConfigChanged,
    McpServerDiscoveryLost,
    McpServerQuarantined,
)


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)


def _discovered(name: str = "probe", *, port: int = 8080) -> DiscoveredMcpServer:
    """A discovered server. `port` varies the definition, and with it the
    fingerprint -- which `create` computes rather than accepts, so a changed
    definition is the only way to get a changed fingerprint."""
    return DiscoveredMcpServer.create(
        name=name,
        source_type="docker",
        mode="http",
        connection_info={"host": "10.88.0.7", "port": port},
        metadata={"runtime_addresses": ["10.88.0.7"]},
    )


@pytest.fixture
def orchestrator_and_bus():
    bus = _RecordingBus()
    config = DiscoveryConfig()
    config.security.require_health_check = False
    orchestrator = DiscoveryOrchestrator(config=config, event_bus=bus)

    async def _accept(_server: Any) -> bool:
        return True

    orchestrator.on_register = _accept
    return orchestrator, bus


def _of_type(bus: _RecordingBus, event_type: type) -> list[Any]:
    return [e for e in bus.published if isinstance(e, event_type)]


@pytest.mark.asyncio
class TestWhatDiscoveryRecords:
    async def test_a_new_server_is_recorded_as_discovered(self, orchestrator_and_bus) -> None:
        orchestrator, bus = orchestrator_and_bus

        server = _discovered()

        assert await orchestrator._process_mcp_server(server) == "registered"

        [event] = _of_type(bus, McpServerDiscovered)
        assert event.mcp_server_name == "probe"
        assert event.source_type == "docker"
        assert event.fingerprint == server.fingerprint

    async def test_the_discovery_is_recorded_before_the_registration(self) -> None:
        # Both rows land in one stream, so their order is the story the log
        # tells. Emitting after `on_register` produced a history whose first row
        # was the registration and whose second was the discovery that caused
        # it -- reproduced on a live gateway before this was moved.
        bus = _RecordingBus()
        config = DiscoveryConfig()
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, event_bus=bus)
        seen_at_registration: list[int] = []

        async def _accept(_server: Any) -> bool:
            seen_at_registration.append(len(bus.published))
            return True

        orchestrator.on_register = _accept

        await orchestrator._process_mcp_server(_discovered())

        assert seen_at_registration == [1], "the discovery must already be recorded when registration is attempted"
        assert isinstance(bus.published[0], McpServerDiscovered)

    async def test_a_server_the_control_plane_refuses_is_still_recorded(self) -> None:
        # Discovery genuinely saw it. The absence of a registration after this
        # row is precisely what an operator needs in order to notice.
        bus = _RecordingBus()
        config = DiscoveryConfig()
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, event_bus=bus)

        async def _refuse(_server: Any) -> bool:
            return False

        orchestrator.on_register = _refuse

        assert await orchestrator._process_mcp_server(_discovered()) == "skipped"

        assert len(_of_type(bus, McpServerDiscovered)) == 1

    async def test_a_changed_definition_carries_both_fingerprints(self, orchestrator_and_bus) -> None:
        # The only moment both exist: a beat later the tracked copy has been
        # overwritten and the old fingerprint is gone for good.
        orchestrator, bus = orchestrator_and_bus
        first = _discovered(port=8080)
        second = _discovered(port=9090)
        await orchestrator._process_mcp_server(first)

        await orchestrator._process_mcp_server(second)

        [event] = _of_type(bus, McpServerDiscoveryConfigChanged)
        assert (event.old_fingerprint, event.new_fingerprint) == (first.fingerprint, second.fingerprint)

    async def test_an_unchanged_server_records_nothing(self, orchestrator_and_bus) -> None:
        # Every source re-reports everything it can see on every cycle. If a
        # re-sighting wrote a row, the log would be a poll transcript.
        orchestrator, bus = orchestrator_and_bus
        await orchestrator._process_mcp_server(_discovered())
        before = len(bus.published)

        assert await orchestrator._process_mcp_server(_discovered()) == "skipped"

        assert len(bus.published) == before

    async def test_a_refusal_is_recorded_with_its_reason(self, orchestrator_and_bus) -> None:
        # The discovery event most worth keeping: something asked to join the
        # fleet and was turned away, and by which rule.
        orchestrator, bus = orchestrator_and_bus
        orchestrator.config.security.max_mcp_servers_per_source = 0

        await orchestrator._process_mcp_server(_discovered())

        [event] = _of_type(bus, McpServerQuarantined)
        assert event.mcp_server_name == "probe"
        assert event.reason
        assert event.validation_result

    async def test_a_refusal_is_recorded_once_not_once_per_cycle(self, orchestrator_and_bus) -> None:
        # Sources re-report a refused server forever, and it is refused every
        # time. Recording each refusal writes a row per cycle -- 2880 a day at
        # the default refresh, all identical. Caught on a live cluster, where a
        # single denied pod had four rows within twenty seconds.
        orchestrator, bus = orchestrator_and_bus
        orchestrator.config.security.max_mcp_servers_per_source = 0

        await orchestrator._process_mcp_server(_discovered())
        await orchestrator._process_mcp_server(_discovered())
        await orchestrator._process_mcp_server(_discovered())

        assert len(_of_type(bus, McpServerQuarantined)) == 1

    async def test_a_server_going_away_is_recorded(self, orchestrator_and_bus) -> None:
        orchestrator, bus = orchestrator_and_bus
        await orchestrator._process_mcp_server(_discovered())

        await orchestrator._handle_deregister("probe", "ttl_expired")

        [event] = _of_type(bus, McpServerDiscoveryLost)
        assert event.mcp_server_name == "probe"
        assert event.reason == "ttl_expired"


@pytest.mark.asyncio
class TestRecordingNeverBreaksDiscovery:
    async def test_a_cycle_survives_a_bus_that_throws(self) -> None:
        class BrokenBus:
            def publish(self, _event: Any) -> None:
                raise RuntimeError("bus is down")

        config = DiscoveryConfig()
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, event_bus=BrokenBus())

        async def _accept(_server: Any) -> bool:
            return True

        orchestrator.on_register = _accept

        assert await orchestrator._process_mcp_server(_discovered()) == "registered"

    async def test_no_bus_is_not_an_error(self) -> None:
        # The orchestrator is constructed without one in plenty of tests, and
        # bootstrap may reach it before the runtime exists.
        config = DiscoveryConfig()
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config)

        async def _accept(_server: Any) -> bool:
            return True

        orchestrator.on_register = _accept

        assert await orchestrator._process_mcp_server(_discovered()) == "registered"


class TestTheseEventsReachTheLog:
    def test_a_discovery_event_routes_to_the_server_stream(self) -> None:
        # These carry `mcp_server_name` rather than `mcp_server_id`, so without
        # the name being recognised as an aggregate identity they would be
        # delivered and dropped -- emitted, and still absent from the log.
        from mcp_hangar.stream_ids import stream_id_for_event

        event = McpServerDiscovered(mcp_server_name="probe", source_type="docker", mode="http", fingerprint="fp")

        assert stream_id_for_event(event) == "mcp_server:probe"

    def test_it_is_the_same_stream_the_registration_lands_in(self) -> None:
        # Discovery names a server before it has an id, and that name is the id
        # it is registered under. One subject, one stream -- so a history reads
        # discovered, registered, started rather than splitting in two.
        from mcp_hangar.domain.events import McpServerRegistered
        from mcp_hangar.stream_ids import stream_id_for_event

        discovered = McpServerDiscovered(mcp_server_name="probe", source_type="docker", mode="http", fingerprint="fp")
        registered = McpServerRegistered(mcp_server_id="probe", source="discovery:docker", mode="remote")

        assert stream_id_for_event(discovered) == stream_id_for_event(registered)


def test_the_orchestrator_is_given_a_bus_in_bootstrap() -> None:
    """A wiring that exists and is never connected is this project's recurring bug.

    The emitters above are correct and inert unless bootstrap passes a bus, and
    that is one keyword argument in a different file -- exactly the shape that
    has shipped dead four times here.
    """
    import inspect

    from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

    source = inspect.getsource(bootstrap_discovery.create_discovery_orchestrator)
    assert "event_bus=" in source, "the orchestrator is built without a bus; discovery would record nothing"
