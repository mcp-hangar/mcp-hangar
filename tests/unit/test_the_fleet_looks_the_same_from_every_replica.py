"""A server registered on one replica is servable on the others.

The last piece of one fleet seen from three places. A server registered on A --
by an operator, or by whichever replica is running discovery -- lived in A's
memory alone, so B and C answered "no such server" for it until they restarted.
Which replica knew about which server depended on where the load balancer had
sent each registration.

The event carries the id; the shared record carries the configuration. That
works because registration writes the record *before* it publishes (#794), so
the row is committed by the time the event exists -- an ordering chosen then for
a different reason, which this now depends on.
"""

from __future__ import annotations

import asyncio

import pytest

from mcp_hangar.application.event_handlers.fleet_projection import FleetProjection
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.events import McpServerDeregistered, McpServerRegistered
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.config_repository import InMemoryMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore


class _SyncLoop:
    """Runs the projection's read inline, so tests need no background thread."""

    def run(self, coro, timeout):
        return asyncio.run(coro)


class _Replica:
    """One gateway: its own fleet and bus, over a shared log and a shared record."""

    def __init__(self, instance_id: str, log: InMemoryEventStore, configs) -> None:
        self.instance_id = instance_id
        self.fleet = InMemoryMcpServerRepository()
        self.configs = configs
        self.bus = EventBus()
        self.bus.set_event_store(log)
        projection = FleetProjection(self.fleet, configs, _SyncLoop())
        self.bus.subscribe(McpServerRegistered, projection.handle, kind=HandlerKind.PROJECTION)
        self.bus.subscribe(McpServerDeregistered, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.bus, instance_id)

    def registers(self, mcp_server_id: str, description: str = "") -> None:
        """Register as the command handler does: record first, then announce."""
        from mcp_hangar.domain.model import McpServer

        asyncio.run(
            self.configs.save(
                McpServerConfigSnapshot(
                    mcp_server_id=mcp_server_id, mode="subprocess", command=["python"], description=description
                )
            )
        )
        self.fleet.add(mcp_server_id, McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python"]))
        self.bus.publish(McpServerRegistered(mcp_server_id=mcp_server_id, source="api", mode="subprocess"))

    def deregisters(self, mcp_server_id: str) -> None:
        asyncio.run(self.configs.delete(mcp_server_id))
        self.fleet.remove(mcp_server_id)
        self.bus.publish(McpServerDeregistered(mcp_server_id=mcp_server_id, source="api"))

    def knows(self, mcp_server_id: str) -> bool:
        return self.fleet.exists(mcp_server_id)


@pytest.fixture
def replicas():
    log = InMemoryEventStore()
    configs = InMemoryMcpServerConfigRepository()
    return _Replica("gateway-a", log, configs), _Replica("gateway-b", log, configs)


class TestARegistrationOnOneReplicaReachesTheOthers:
    def test_the_peer_knows_about_it(self, replicas) -> None:
        # The defect: B answered "no such server" for something A had just
        # registered, and would keep doing so until it restarted.
        a, b = replicas

        a.registers("math")
        b.tailer.tick()

        assert b.knows("math") is True

    def test_the_peer_gets_the_configuration_and_not_just_the_name(self, replicas) -> None:
        # The event carries an id, a source and a mode. Everything else comes
        # from the shared record -- which is why the event does not have to be
        # versioned every time a configuration field is added.
        a, b = replicas

        a.registers("math", description="adds numbers")
        b.tailer.tick()

        assert b.fleet.get("math").description == "adds numbers"

    def test_a_deregistration_reaches_them_too(self, replicas) -> None:
        a, b = replicas
        a.registers("math")
        b.tailer.tick()

        a.deregisters("math")
        b.tailer.tick()

        assert b.knows("math") is False

    def test_the_registering_replica_keeps_its_own_copy(self, replicas) -> None:
        # Its own event is skipped by its own tail, so the projection cannot
        # replace a server this replica may be running with a COLD rebuild.
        a, _b = replicas
        a.registers("math")

        a.tailer.tick()

        assert a.knows("math") is True


class TestApplyingItTwiceIsSafe:
    def test_a_repeated_event_leaves_the_local_copy_alone(self, replicas) -> None:
        # The tail is at-least-once. A server already in the fleet may be
        # *running*; the record describes configuration, not state, so
        # rebuilding it would undo the thing being projected.
        a, b = replicas
        a.registers("math")
        b.tailer.tick()
        before = b.fleet.get("math")

        b.bus.deliver_tailed(McpServerRegistered(mcp_server_id="math", source="api", mode="subprocess"))

        assert b.fleet.get("math") is before

    def test_deregistering_something_absent_is_not_an_error(self, replicas) -> None:
        _a, b = replicas

        b.bus.deliver_tailed(McpServerDeregistered(mcp_server_id="never-existed", source="api"))

        assert b.knows("never-existed") is False


class TestWhenTheRecordIsMissing:
    def test_it_declines_to_guess(self, replicas) -> None:
        # Registration writes the record before it publishes, so absence means
        # something unusual: no durable storage, or a registration rolled back
        # after its event escaped. Inventing a configuration would put a server
        # in the fleet that nobody described.
        _a, b = replicas

        b.bus.deliver_tailed(McpServerRegistered(mcp_server_id="ghost", source="api", mode="subprocess"))

        assert b.knows("ghost") is False

    def test_an_unreadable_record_does_not_stall_the_tail(self, replicas) -> None:
        class _Broken:
            async def get(self, mcp_server_id: str):
                raise RuntimeError("the database is unreachable")

        _a, b = replicas
        projection = FleetProjection(b.fleet, _Broken(), _SyncLoop())

        projection.handle(McpServerRegistered(mcp_server_id="math", source="api", mode="subprocess"))

        assert b.knows("math") is False


class TestTheProjectionIsWiredThatWay:
    def test_both_subscriptions_are_projections(self) -> None:
        # An effect would run only on the replica that produced the event --
        # which is the state this fixes.
        import inspect

        from mcp_hangar.server.bootstrap import event_handlers

        lines = [
            line
            for line in inspect.getsource(event_handlers.init_event_handlers).splitlines()
            if "fleet_projection.handle" in line
        ]

        assert len(lines) == 2
        for line in lines:
            assert "HandlerKind.PROJECTION" in line, line

    def test_it_is_not_wired_without_a_durable_record(self) -> None:
        # There would be nothing to read, and no peers to learn from either.
        import inspect

        from mcp_hangar.server.bootstrap import event_handlers

        source = inspect.getsource(event_handlers.init_event_handlers)

        assert "InMemoryMcpServerConfigRepository" in source

    def test_recovery_and_the_projection_rebuild_a_server_the_same_way(self) -> None:
        # Two copies of "how to turn a snapshot back into a server" would drift
        # the first time a configuration field was added, and the drift shows up
        # as a field that is simply absent on replicas that learned by tail.
        import inspect

        from mcp_hangar.infrastructure.persistence import recovery_service

        source = inspect.getsource(recovery_service.RecoveryService._create_mcp_server_from_config)

        assert "server_from_snapshot(config)" in source
