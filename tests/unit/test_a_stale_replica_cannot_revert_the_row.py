"""A replica whose copy is behind cannot write its stale copy over the shared row (#1954).

Every writer of a server's configuration row used to save a snapshot of the
replica's whole local copy, and `save()` overwrote whatever was there. So a
replica that had not yet tailed a peer's change -- a tighter L7 egress policy,
say -- wrote the older policy back with any unrelated update, and the next
restart rebuilt every replica with it.

The fix has two halves, and both are pinned here. A change is built on the
recorded row, not on the local copy, so an unrelated update carries the peer's
policy along. And the write is a compare-and-set, so a row that changed between
the read and the write refuses the write instead of losing the change.
"""

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.commands.crud_commands import (
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    SetL7PolicyCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    SetL7PolicyHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.domain.contracts.fleet import IFleetWriter
from mcp_hangar.domain.contracts.persistence import ConcurrentModificationError, McpServerConfigSnapshot
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy, ToolRules
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.backends.postgresql.config_repository import (
    PostgresMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.config_repository import (
    InMemoryMcpServerConfigRepository,
    SQLiteMcpServerConfigRepository,
)
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter

_TIGHT = L7Policy(tools=ToolRules(deny=("delete_*",)))


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


def _server(mcp_server_id: str = "files") -> McpServer:
    return McpServer(mcp_server_id=mcp_server_id, mode="subprocess", command=["python", "-m", "files"])


class _Replica:
    """One gateway: its own fleet in memory, the shared row through a fleet writer."""

    def __init__(self, writer: IFleetWriter, *servers: McpServer) -> None:
        self.fleet = InMemoryMcpServerRepository()
        for server in servers:
            self.fleet.add(server.mcp_server_id, server)
        self.create = CreateMcpServerHandler(repository=self.fleet, event_bus=_Bus(), fleet_writer=writer)
        self.update = UpdateMcpServerHandler(repository=self.fleet, event_bus=_Bus(), fleet_writer=writer)
        self.set_l7 = SetL7PolicyHandler(repository=self.fleet, event_bus=_Bus(), fleet_writer=writer)
        self.delete = DeleteMcpServerHandler(repository=self.fleet, event_bus=_Bus(), fleet_writer=writer)


@pytest.fixture
def shared_row(tmp_path):
    """One SQLite configuration table, which two replicas write through."""
    database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
    asyncio.run(database.initialize())
    repository = SQLiteMcpServerConfigRepository(database)
    writer = RepositoryFleetWriter(repository)
    yield repository, writer
    writer.close()


def _row(repository, mcp_server_id: str = "files") -> McpServerConfigSnapshot | None:
    return asyncio.run(repository.get(mcp_server_id))


class TestAnUpdateOnABehindReplicaKeepsThePeersPolicy:
    def test_an_unrelated_update_does_not_write_the_older_policy_back(self, shared_row) -> None:
        repository, writer = shared_row
        a = _Replica(writer)
        a.create.handle(
            CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "files"])
        )
        # B registered the same server before A tightened its policy, and has
        # not yet tailed the change: its copy carries no policy.
        b = _Replica(writer, _server())

        a.set_l7.handle(SetL7PolicyCommand(mcp_server_id="files", policy=_TIGHT, source="operator"))
        b.update.handle(UpdateMcpServerCommand(mcp_server_id="files", description="renamed"))

        row = _row(repository)
        assert row is not None
        assert row.description == "renamed"
        assert row.l7_policy == _TIGHT.to_wire()

    def test_a_stale_policy_delivery_does_not_write_the_older_description_back(self, shared_row) -> None:
        repository, writer = shared_row
        a = _Replica(writer)
        a.create.handle(
            CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "files"])
        )
        b = _Replica(writer, _server())

        a.update.handle(UpdateMcpServerCommand(mcp_server_id="files", description="renamed"))
        b.set_l7.handle(SetL7PolicyCommand(mcp_server_id="files", policy=_TIGHT, source="operator"))

        row = _row(repository)
        assert row is not None
        assert row.description == "renamed"
        assert row.l7_policy == _TIGHT.to_wire()


class TestARowThatChangedAfterTheReadRefusesTheWrite:
    def test_the_update_is_refused_and_the_server_is_left_as_it_was(self) -> None:
        recorded = McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess", description="before")
        writer = MagicMock(spec=IFleetWriter)
        writer.read.return_value = (recorded, 3)
        writer.save_versioned.side_effect = ConcurrentModificationError("files", 3, 4)
        server = _server()
        replica = _Replica(writer, server)

        with pytest.raises(ConcurrentModificationError):
            replica.update.handle(UpdateMcpServerCommand(mcp_server_id="files", description="after"))

        assert writer.save_versioned.call_args.args[1] == 3
        assert server.description != "after"
        assert server.collect_events() == []

    def test_the_policy_is_refused_and_the_server_does_not_take_it(self) -> None:
        recorded = McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess")
        writer = MagicMock(spec=IFleetWriter)
        writer.read.return_value = (recorded, 7)
        writer.save_versioned.side_effect = ConcurrentModificationError("files", 7, 8)
        server = _server()
        replica = _Replica(writer, server)

        with pytest.raises(ConcurrentModificationError):
            replica.set_l7.handle(SetL7PolicyCommand(mcp_server_id="files", policy=_TIGHT, source="operator"))

        assert server.l7_policy is None

    def test_a_bad_value_is_refused_before_anything_is_written(self) -> None:
        writer = MagicMock(spec=IFleetWriter)
        replica = _Replica(writer, _server())

        with pytest.raises(Exception):  # noqa: B017 -- the value object's own validation error
            replica.update.handle(UpdateMcpServerCommand(mcp_server_id="files", idle_ttl_s=-1))

        writer.save_versioned.assert_not_called()


class TestCreateNeverOverwritesALiveRow:
    def test_a_replica_that_has_not_heard_of_the_server_is_refused(self, shared_row) -> None:
        repository, writer = shared_row
        create = CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "files"])
        _Replica(writer).create.handle(create)
        b = _Replica(writer)  # a second replica that has not tailed the create

        with pytest.raises(ValidationError):
            b.create.handle(
                CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "other"])
            )

        row = _row(repository)
        assert row is not None
        assert row.command == ["python", "-m", "files"]

    def test_a_deleted_server_can_be_created_again(self, shared_row) -> None:
        repository, writer = shared_row
        replica = _Replica(writer)
        create = CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "files"])
        replica.create.handle(create)
        replica.delete.handle(DeleteMcpServerCommand(mcp_server_id="files"))

        replica.create.handle(create)

        assert asyncio.run(repository.exists("files"))

    def test_an_update_after_a_peer_deleted_the_server_does_not_bring_it_back(self, shared_row) -> None:
        repository, writer = shared_row
        a = _Replica(writer)
        a.create.handle(
            CreateMcpServerCommand(mcp_server_id="files", mode="subprocess", command=["python", "-m", "files"])
        )
        b = _Replica(writer, _server())
        a.delete.handle(DeleteMcpServerCommand(mcp_server_id="files"))

        with pytest.raises(ConcurrentModificationError):
            b.update.handle(UpdateMcpServerCommand(mcp_server_id="files", description="renamed"))

        assert not asyncio.run(repository.exists("files"))


class TestTheSQLiteRowIsACompareAndSet:
    def test_a_write_against_an_old_version_is_refused(self, shared_row) -> None:
        repository, _ = shared_row
        snapshot = McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess")
        asyncio.run(repository.save_versioned(snapshot, None))
        _, version = asyncio.run(repository.get_with_version("files"))
        asyncio.run(repository.save_versioned(snapshot, version))

        with pytest.raises(ConcurrentModificationError):
            asyncio.run(repository.save_versioned(snapshot, version))

    def test_a_deleted_row_is_not_read_as_live(self, shared_row) -> None:
        repository, _ = shared_row
        asyncio.run(repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess"), None))
        asyncio.run(repository.delete("files"))

        assert asyncio.run(repository.get_with_version("files")) is None


class TestTheInMemoryRepositoryKeepsTheSameContract:
    def test_create_refuses_a_live_row_and_an_old_version_is_refused(self) -> None:
        repository = InMemoryMcpServerConfigRepository()
        snapshot = McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess")
        asyncio.run(repository.save_versioned(snapshot, None))

        with pytest.raises(ConcurrentModificationError):
            asyncio.run(repository.save_versioned(snapshot, None))
        _, version = asyncio.run(repository.get_with_version("files"))
        asyncio.run(repository.save_versioned(snapshot, version))
        with pytest.raises(ConcurrentModificationError):
            asyncio.run(repository.save_versioned(snapshot, version))


def _postgres() -> tuple[PostgresMcpServerConfigRepository, MagicMock, MagicMock]:
    connection = MagicMock()
    cursor = MagicMock()
    connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
    connection.cursor.return_value.__exit__ = Mock(return_value=False)

    class _Factory:
        @contextmanager
        def get_connection(self):
            yield connection

    repository = PostgresMcpServerConfigRepository(connection_factory=_Factory())
    connection.reset_mock()
    cursor.reset_mock()
    return repository, connection, cursor


class TestThePostgresRowIsACompareAndSet:
    def test_an_update_matches_the_version_and_a_live_row(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.rowcount = 1

        asyncio.run(repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess"), 4))

        sql, params = cursor.execute.call_args.args
        assert "WHERE mcp_server_id = %s AND version = %s AND enabled = %s" in sql
        assert "version = version + 1" in sql
        assert params[-3:] == ("files", 4, True)
        connection.commit.assert_called_once()

    def test_no_matching_row_is_a_conflict_and_rolls_back(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.rowcount = 0

        with pytest.raises(ConcurrentModificationError):
            asyncio.run(repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess"), 4))

        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_a_create_over_a_live_row_is_a_conflict(self) -> None:
        repository, _, cursor = _postgres()
        cursor.fetchone.return_value = (2, True)

        with pytest.raises(ConcurrentModificationError):
            asyncio.run(
                repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess"), None)
            )

    def test_two_creates_racing_on_the_primary_key_is_a_conflict(self) -> None:
        repository, _, cursor = _postgres()

        class _UniqueViolation(Exception):
            pgcode = "23505"

        cursor.execute.side_effect = _UniqueViolation("duplicate key")

        with pytest.raises(ConcurrentModificationError):
            asyncio.run(repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="files", mode="subprocess"), 0))

    def test_a_deleted_row_is_not_read_as_live(self) -> None:
        repository, _, cursor = _postgres()
        cursor.fetchone.return_value = None

        assert asyncio.run(repository.get_with_version("files")) is None
        assert "AND enabled = TRUE" in cursor.execute.call_args.args[0]
