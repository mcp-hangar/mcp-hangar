"""A key-pinned server's key list or endpoint changes on every replica (#1944, ADR-030 §8, T11b).

The projection read a peer's registration and its L7 policy, and left a server
it already held unchanged. A key list removed on one replica therefore stayed
trusted on every other one until it restarted. Now `McpServerUpdated` makes every
replica, the one that handled it included, compare the row with what its live
client was built from, and replace the copy with a COLD one when they differ.
The old copy is retired: it refuses calls, and its client is closed off the tail.
"""

import asyncio
import threading
from typing import Any

import pytest

from mcp_hangar.application.event_handlers.fleet_projection import FleetProjection
from mcp_hangar.application.services.event_tailer import EventTailer
from mcp_hangar.domain.contracts.event_bus import HandlerKind
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.events import McpServerDeregistered, McpServerRegistered, McpServerUpdated
from mcp_hangar.domain.exceptions import McpServerNotReadyError, McpServerStartError
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.mcp_server import McpServerState
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.sqlite_event_store import SQLiteEventStore

ENDPOINT = "https://203.0.113.5:8443"
OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def _tls(*pins: str, key_ref: str = "lineage") -> dict[str, Any]:
    return {"peer_identity": {"key_ref": key_ref, "spki_sha256": list(pins)}}


def _row(*pins: str, endpoint: str = ENDPOINT, tls: dict[str, Any] | None = None) -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(
        mcp_server_id="payments", mode="remote", endpoint=endpoint, tls=tls if tls is not None else _tls(*pins)
    )


class _SyncLoop:
    def run(self, coro, timeout):
        return asyncio.run(coro)


class _Closer:
    def __init__(self) -> None:
        self.closed: list[Any] = []

    def submit(self, coro, on_success=None, on_error=None) -> None:
        asyncio.run(coro)


class _Client:
    def __init__(self) -> None:
        self.closed = False

    def is_alive(self) -> bool:
        return not self.closed

    def close(self) -> None:
        self.closed = True


def _running(server: McpServer, client: _Client) -> McpServer:
    """A copy whose live client was built from its current declaration."""
    server._client = client
    server._connected_with = server.connection_declaration()
    server._state = McpServerState.READY
    return server


def _update() -> McpServerUpdated:
    return McpServerUpdated(mcp_server_id="payments", source="api")


@pytest.fixture
def replica(tmp_path):
    configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    asyncio.run(configs.save_versioned(_row(OLD), None))
    fleet = InMemoryMcpServerRepository()
    client = _Client()
    fleet.add(
        "payments",
        _running(McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=_tls(OLD)), client),
    )
    return fleet, configs, FleetProjection(fleet, configs, _SyncLoop(), closer=_Closer()), client


def _write(configs, row: McpServerConfigSnapshot) -> None:
    recorded = asyncio.run(configs.get_with_version("payments"))
    asyncio.run(configs.save_versioned(row, recorded[1] if recorded else None))


class TestAChangedKeyListReplacesTheCopy:
    def test_the_copy_is_replaced_by_a_cold_one_and_the_old_one_retired(self, replica) -> None:
        fleet, configs, projection, client = replica
        old = fleet.get("payments")
        _write(configs, _row(NEW))

        projection.handle(_update())

        new = fleet.get("payments")
        assert new is not old and new.state is McpServerState.COLD
        assert new._peer_identity.spki_sha256 == frozenset({NEW})
        assert old.is_retired and client.closed
        with pytest.raises(McpServerNotReadyError):
            old.ensure_ready()

    def test_the_projection_publishes_nothing(self, replica) -> None:
        fleet, configs, projection, _ = replica
        old = fleet.get("payments")
        _write(configs, _row(NEW))

        projection.handle(_update())

        assert old.collect_events() == [] and fleet.get("payments").collect_events() == []

    def test_an_endpoint_change_replaces_it_too(self, replica) -> None:
        fleet, configs, projection, _ = replica
        _write(configs, _row(OLD, endpoint="https://203.0.113.9:8443"))

        projection.handle(_update())

        assert fleet.get("payments")._endpoint == "https://203.0.113.9:8443"

    def test_the_same_row_delivered_again_replaces_nothing(self, replica) -> None:
        fleet, _, projection, client = replica
        old = fleet.get("payments")

        projection.handle(_update())
        projection.handle(_update())

        assert fleet.get("payments") is old and not client.closed

    def test_the_handling_replica_compares_against_its_client_not_its_aggregate(self, replica) -> None:
        fleet, configs, projection, client = replica
        old = fleet.get("payments")
        # The handling replica's aggregate already carries the new list; its
        # pooled connection was built from the old one.
        old._tls_config = _tls(NEW)
        _write(configs, _row(NEW))

        projection.handle(_update())

        assert fleet.get("payments") is not old and client.closed

    def test_an_update_of_a_plain_server_is_left_to_its_own_issue(self, tmp_path) -> None:
        configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
        asyncio.run(configs.save_versioned(_row(tls={}), None))
        fleet = InMemoryMcpServerRepository()
        plain = McpServer(mcp_server_id="payments", mode="remote", endpoint="https://203.0.113.7:8443")
        fleet.add("payments", plain)

        FleetProjection(fleet, configs, _SyncLoop(), closer=_Closer()).handle(_update())

        assert fleet.get("payments") is plain


class _Unreadable:
    """The shared store, failing until told otherwise."""

    def __init__(self, configs) -> None:
        self.configs = configs
        self.failing = True

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.configs, name)
        if not self.failing:
            return method

        async def fail(*args: Any, **kwargs: Any) -> Any:
            raise OSError("the database is unreachable")

        return fail


class TestAnUnreadableRowFailsClosed:
    def test_the_copy_is_retired_and_refuses_until_a_later_read_succeeds(self, replica) -> None:
        fleet, configs, _, client = replica
        store = _Unreadable(configs)
        projection = FleetProjection(fleet, store, _SyncLoop(), closer=_Closer())
        old = fleet.get("payments")

        projection.handle(_update())

        assert fleet.get("payments") is old and old.is_retired and client.closed
        with pytest.raises(McpServerNotReadyError):
            old.ensure_ready()

        store.failing = False
        projection.handle(McpServerRegistered(mcp_server_id="unrelated", source="api", mode="remote"))

        restored = fleet.get("payments")
        assert restored is not old and not restored.is_retired and restored.state is McpServerState.COLD

    def test_a_deregistration_forgets_the_pending_read(self, replica) -> None:
        fleet, configs, _, _ = replica
        store = _Unreadable(configs)
        projection = FleetProjection(fleet, store, _SyncLoop(), closer=_Closer())
        projection.handle(_update())

        projection.handle(McpServerDeregistered(mcp_server_id="payments", source="api"))

        assert projection._unread == set()


class TestAPlainRowUnderARecordedName:
    def _plain_row_past_the_record(self, configs) -> None:
        # An older replica's create: the old upsert, which never reads the record.
        asyncio.run(configs.save(_row(tls={})))

    def test_an_update_to_it_retires_the_key_pinned_copy(self, replica) -> None:
        fleet, configs, projection, client = replica
        old = fleet.get("payments")
        self._plain_row_past_the_record(configs)

        projection.handle(_update())

        assert fleet.get("payments") is old and old.is_retired and client.closed

    def test_a_registration_of_it_is_not_applied(self, replica) -> None:
        fleet, configs, projection, _ = replica
        fleet.remove("payments")
        self._plain_row_past_the_record(configs)

        projection.handle(McpServerRegistered(mcp_server_id="payments", source="api", mode="remote"))

        assert not fleet.exists("payments")


class TestAStartUnderWayWhenRetired:
    def test_it_refuses_and_closes_the_client_it_launched(self, monkeypatch) -> None:
        server = McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=_tls(OLD))
        launched = _Client()
        connecting, retired = threading.Event(), threading.Event()

        def create_client() -> _Client:
            connecting.set()
            retired.wait(5)
            return launched

        monkeypatch.setattr(server, "_create_client", create_client)
        monkeypatch.setattr(server, "_perform_mcp_handshake", lambda client: None)
        outcome: list[BaseException] = []

        def start() -> None:
            try:
                server.ensure_ready()
            except BaseException as error:  # noqa: BLE001 -- the test reads it
                outcome.append(error)

        starter = threading.Thread(target=start)
        starter.start()
        assert connecting.wait(5)
        assert server.retire() is None
        retired.set()
        starter.join(5)

        assert outcome and isinstance(outcome[0], McpServerStartError)
        assert launched.closed
        assert server._client is None


class TestAPeersUpdateArrivesThroughTheTail:
    """The way a peer actually learns of it: the shared log, the tailer, the bus, as bootstrap wires them."""

    def test_the_tailed_event_replaces_the_copy(self, replica, tmp_path) -> None:
        fleet, configs, projection, client = replica
        log = SQLiteEventStore(tmp_path / "events.db")
        here = EventBus()
        here.set_event_store(log)
        here.subscribe(McpServerUpdated, projection.handle, kind=HandlerKind.PROJECTION)
        tailer = EventTailer(log, here, "gateway-here")
        old = fleet.get("payments")

        _write(configs, _row(NEW))
        update = _update()
        object.__setattr__(update, "produced_by", "gateway-peer")
        log.append("mcp_server:payments", [update], expected_version=-1)
        tailer.tick()

        assert fleet.get("payments") is not old and old.is_retired and client.closed


class TestARetiredCopyWritesNothingToTheStream:
    def test_a_health_check_on_it_records_no_event(self, replica) -> None:
        fleet, configs, projection, _ = replica
        old = fleet.get("payments")
        old.collect_events()
        _write(configs, _row(NEW))
        projection.handle(_update())

        old.health_check()
        old.shutdown()

        assert old.collect_events() == []


class TestAPlainCopyWhoseRowGainedAKeyIsNotForgotten:
    def test_an_unreadable_row_is_read_again_and_applied(self, tmp_path) -> None:
        configs = SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
        asyncio.run(configs.save_versioned(_row(tls={}), None))
        fleet = InMemoryMcpServerRepository()
        plain = McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT)
        fleet.add("payments", plain)
        store = _Unreadable(configs)
        projection = FleetProjection(fleet, store, _SyncLoop(), closer=_Closer())
        # The move of section 4f: the row now declares a key list.
        store.failing = False
        _write(configs, _row(NEW))
        store.failing = True

        projection.handle(_update())
        assert fleet.get("payments") is plain

        store.failing = False
        projection.handle(McpServerRegistered(mcp_server_id="unrelated", source="api", mode="remote"))

        assert fleet.get("payments")._peer_identity.spki_sha256 == frozenset({NEW})
