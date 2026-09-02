"""A tool withdrawn on one replica is withdrawn on all of them, and after a restart.

`POST /admin/tools/{server}/{name}/withdraw` wrote a dict in one process. On a
fleet of N the other N-1 kept listing and serving the tool, so an agent reached
it by retrying until the load balancer picked a different pod, and a rolling
restart lifted the withdrawal entirely -- while the response said
`{"withdrawn": true}` (#1165).

The last test is the one the suspension projection cannot make: a withdrawal is
recorded in its own stream, so a replica that starts later reads it back rather
than coming up serving what the fleet has already withdrawn.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.event_handlers.withdrawal_projection import WithdrawalProjection
from mcp_hangar.application.read_models.tool_projection import ToolProjectionRegistry
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.events import ToolRestored, ToolWithdrawn
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.server.bootstrap.withdrawals import restore_runtime_withdrawals
from mcp_hangar.stream_ids import TOOL_WITHDRAWAL, stream_id_for

_SERVER = "github"
_TOOL = "delete_repo"
_TENANT = "tenant:a"


class _Replica:
    """One gateway: its own withdrawal overlay and bus, over a shared log."""

    def __init__(self, instance_id: str, log: InMemoryEventStore) -> None:
        self.instance_id = instance_id
        self.registry = ToolProjectionRegistry()
        self.event_bus = EventBus()
        self.event_bus.set_event_store(log)
        projection = WithdrawalProjection(self.registry)
        self.event_bus.subscribe(ToolWithdrawn, projection.handle, kind=HandlerKind.PROJECTION)
        self.event_bus.subscribe(ToolRestored, projection.handle, kind=HandlerKind.PROJECTION)
        self.tailer = EventTailer(log, self.event_bus, instance_id)

    def withdraws(self, tenant_id: str | None = _TENANT, *, kind: str = "tool", tool: str = _TOOL) -> None:
        self.event_bus.publish(ToolWithdrawn(tenant_id=tenant_id, mcp_server=_SERVER, tool=tool, kind=kind))

    def restores(self, tenant_id: str | None = _TENANT, *, kind: str = "tool", tool: str = _TOOL) -> None:
        self.event_bus.publish(ToolRestored(tenant_id=tenant_id, mcp_server=_SERVER, tool=tool, kind=kind))

    def refuses(self, tenant_id: str | None = _TENANT, *, kind: str = "tool", tool: str = _TOOL) -> bool:
        return self.registry.is_withdrawn(_SERVER, tool, kind=kind, tenant_id=tenant_id)


@pytest.fixture
def replicas() -> tuple[_Replica, _Replica]:
    log = InMemoryEventStore()
    return _Replica("gateway-a", log), _Replica("gateway-b", log)


class TestRetryingAgainstAnotherReplicaDoesNotWork:
    def test_the_peer_withdraws_the_tool_too(self, replicas) -> None:
        a, b = replicas

        a.withdraws()
        b.tailer.tick()

        assert a.refuses() is True
        assert b.refuses() is True

    def test_a_prompt_and_a_resource_travel_as_themselves(self, replicas) -> None:
        # #1148 put `kind` on the event so the overlay it rebuilds is the one it
        # was written to. With no consumer, that field went nowhere.
        a, b = replicas

        a.withdraws(kind="prompt", tool="draft_email")
        a.withdraws(kind="resource", tool="demo://secret/1")
        b.tailer.tick()

        assert b.refuses(kind="prompt", tool="draft_email") is True
        assert b.refuses(kind="resource", tool="demo://secret/1") is True
        assert b.refuses(kind="tool", tool="draft_email") is False

    def test_an_all_tenants_withdrawal_reaches_the_peer(self, replicas) -> None:
        a, b = replicas

        a.withdraws(tenant_id=None)
        b.tailer.tick()

        assert b.refuses(tenant_id="tenant:whoever") is True

    def test_restoring_reaches_the_peer_as_well(self, replicas) -> None:
        a, b = replicas
        a.withdraws()
        b.tailer.tick()

        a.restores()
        b.tailer.tick()

        assert a.refuses() is False
        assert b.refuses() is False


class TestARestartDoesNotLiftIt:
    def test_a_replica_that_starts_later_rebuilds_the_overlay(self, replicas) -> None:
        # The whole point of recording it: a rolling restart used to end with
        # the withdrawal held by nobody.
        a, _b = replicas
        a.withdraws()

        late = _Replica("gateway-c", a.event_bus.event_store)
        restore_runtime_withdrawals(late, late.registry)

        assert late.refuses() is True

    def test_a_withdrawal_lifted_before_the_restart_stays_lifted(self, replicas) -> None:
        # Replay is a fold, not a filter: the restore has to be applied in
        # order, or every tool ever withdrawn comes back withdrawn forever.
        a, _b = replicas
        a.withdraws()
        a.restores()

        late = _Replica("gateway-c", a.event_bus.event_store)
        restore_runtime_withdrawals(late, late.registry)

        assert late.refuses() is False

    def test_replaying_twice_is_replaying_once(self, replicas) -> None:
        a, _b = replicas
        a.withdraws()

        late = _Replica("gateway-c", a.event_bus.event_store)
        restore_runtime_withdrawals(late, late.registry)
        restore_runtime_withdrawals(late, late.registry)

        assert late.refuses() is True

    def test_a_store_that_keeps_nothing_is_not_an_error(self, replicas) -> None:
        a, _b = replicas

        no_store = _Replica("gateway-d", a.event_bus.event_store)
        no_store.event_bus.set_event_store(None)

        assert restore_runtime_withdrawals(no_store, no_store.registry) == 0


class TestTheDecisionIsRecorded:
    def test_it_lands_in_its_own_stream_not_the_servers_history(self, replicas) -> None:
        # Sharing the server's stream would mean reading every invocation it has
        # ever served to find the withdrawals at boot.
        a, _b = replicas
        a.withdraws()

        log = a.event_bus.event_store
        assert log.list_streams(prefix=f"{TOOL_WITHDRAWAL}:") == [stream_id_for(TOOL_WITHDRAWAL, _SERVER)]
        assert log.read_stream(stream_id_for(TOOL_WITHDRAWAL, _SERVER))

    def test_the_deciding_replica_applies_it_immediately(self, replicas) -> None:
        a, _b = replicas

        a.withdraws()

        assert a.refuses() is True
