"""The peer-key name record outlives the server it names (#1947, ADR-030 §9).

Deleting a server only disables its configuration row, and the next create of
the name rewrites that row. So the row cannot remember that a name was key-pinned,
and a deleted key-pinned server could come back under its name as a plain one,
whose key nothing checks. The record is a table of its own, written in the same
transaction as the row: one entry per name, `key_ref` unique.
"""

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot, PeerKeyNameRefused
from mcp_hangar.domain.exceptions import ValidationError
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

ENDPOINT = "https://203.0.113.5:8443"


def _tls(key_ref: str) -> dict[str, Any]:
    return {"peer_identity": {"key_ref": key_ref, "spki_sha256": ["sha256:" + "a" * 64]}}


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


class _Allow:
    def may_declare(self, principal: Any) -> bool:
        return True


class _Gateway:
    """One gateway's create and delete handlers over a shared configuration store."""

    def __init__(self, writer: RepositoryFleetWriter) -> None:
        self.fleet = InMemoryMcpServerRepository()
        handlers = {"repository": self.fleet, "event_bus": _Bus(), "fleet_writer": writer}
        self._create = CreateMcpServerHandler(**handlers, peer_identity_authority=_Allow())
        self._delete = DeleteMcpServerHandler(**handlers, peer_identity_authority=_Allow())

    def create(self, name: str, key_ref: str | None = None) -> None:
        tls = _tls(key_ref) if key_ref else None
        self._create.handle(CreateMcpServerCommand(mcp_server_id=name, mode="remote", endpoint=ENDPOINT, tls=tls))

    def delete(self, name: str) -> None:
        self._delete.handle(DeleteMcpServerCommand(mcp_server_id=name))


def _sqlite(tmp_path) -> SQLiteMcpServerConfigRepository:
    database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
    asyncio.run(database.initialize())
    return SQLiteMcpServerConfigRepository(database)


@pytest.fixture(params=["sqlite", "memory"])
def store(request, tmp_path):
    repository = _sqlite(tmp_path) if request.param == "sqlite" else InMemoryMcpServerConfigRepository()
    writer = RepositoryFleetWriter(repository)
    yield repository, writer
    writer.close()


class TestTheRecordOutlivesTheServer:
    def test_a_deleted_key_pinned_server_does_not_come_back_plain(self, store) -> None:
        repository, writer = store
        gateway = _Gateway(writer)
        gateway.create("payments", "lineage")
        gateway.delete("payments")

        with pytest.raises(ValidationError, match="peer-key name record with key_ref 'lineage'"):
            _Gateway(writer).create("payments")

        assert asyncio.run(repository.get_with_version("payments")) is None

    def test_it_comes_back_with_the_same_key_ref(self, store) -> None:
        repository, writer = store
        gateway = _Gateway(writer)
        gateway.create("payments", "lineage")
        gateway.delete("payments")

        _Gateway(writer).create("payments", "lineage")

        row = asyncio.run(repository.get("payments"))
        assert row is not None and row.tls == _tls("lineage")

    def test_another_key_ref_under_the_name_is_refused(self, store) -> None:
        _, writer = store
        gateway = _Gateway(writer)
        gateway.create("payments", "lineage")
        gateway.delete("payments")

        with pytest.raises(ValidationError, match="with key_ref 'lineage'"):
            _Gateway(writer).create("payments", "another")


class TestAKeyRefNamesOneServer:
    def test_a_second_name_cannot_claim_it_and_nothing_is_written(self, store) -> None:
        repository, writer = store
        _Gateway(writer).create("payments", "lineage")

        with pytest.raises(ValidationError, match="key_ref 'lineage' already names the server 'payments'"):
            _Gateway(writer).create("billing", "lineage")

        assert asyncio.run(repository.get("billing")) is None

    def test_a_plain_server_under_an_unrecorded_name_is_unaffected(self, store) -> None:
        repository, writer = store
        _Gateway(writer).create("payments", "lineage")

        _Gateway(writer).create("billing")

        assert asyncio.run(repository.get("billing")) is not None


def test_the_record_survives_a_restart(tmp_path) -> None:
    writer = RepositoryFleetWriter(_sqlite(tmp_path))
    gateway = _Gateway(writer)
    gateway.create("payments", "lineage")
    gateway.delete("payments")
    writer.close()

    restarted = RepositoryFleetWriter(_sqlite(tmp_path))
    try:
        with pytest.raises(ValidationError, match="peer-key name record"):
            _Gateway(restarted).create("payments")
    finally:
        restarted.close()


class TestWithoutADurableStore:
    def test_declaring_a_peer_identity_is_refused(self) -> None:
        fleet = InMemoryMcpServerRepository()
        handler = CreateMcpServerHandler(fleet, _Bus(), peer_identity_authority=_Allow())

        with pytest.raises(ValidationError, match="needs a durable configuration store"):
            handler.handle(
                CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=_tls("k"))
            )

        assert fleet.get("payments") is None

    def test_a_plain_server_is_still_created(self) -> None:
        fleet = InMemoryMcpServerRepository()

        CreateMcpServerHandler(fleet, _Bus()).handle(
            CreateMcpServerCommand(mcp_server_id="plain", mode="remote", endpoint=ENDPOINT)
        )

        assert fleet.get("plain") is not None


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


def _pinned(name: str = "payments", key_ref: str = "lineage") -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=name, mode="remote", endpoint=ENDPOINT, tls=_tls(key_ref))


class TestThePostgresClaimIsInTheRowsTransaction:
    """Concurrency on a real PostgreSQL is the live tier's (#1949); this pins the statements."""

    def test_the_schema_creates_the_record(self) -> None:
        connection = MagicMock()
        cursor = MagicMock()
        connection.cursor.return_value.__enter__ = Mock(return_value=cursor)
        connection.cursor.return_value.__exit__ = Mock(return_value=False)

        class _Factory:
            @contextmanager
            def get_connection(self):
                yield connection

        PostgresMcpServerConfigRepository(connection_factory=_Factory())

        ddl = cursor.execute.call_args.args[0]
        assert "CREATE TABLE IF NOT EXISTS peer_key_names" in ddl
        assert "key_ref TEXT NOT NULL UNIQUE" in ddl

    def test_the_claim_is_inserted_then_read_back_on_both_columns_before_the_row(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.fetchall.return_value = [("payments", "lineage")]

        asyncio.run(repository.save_versioned(_pinned(), 0))

        statements = [c.args[0] for c in cursor.execute.call_args_list]
        assert "ON CONFLICT DO NOTHING" in statements[0]
        assert "WHERE name = %s OR key_ref = %s" in statements[1]
        assert "INSERT INTO mcp_server_configs" in statements[2]
        connection.commit.assert_called_once()

    def test_a_claim_another_name_holds_rolls_back_without_writing_the_row(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.fetchall.return_value = [("payments", "lineage")]

        with pytest.raises(PeerKeyNameRefused, match="already names the server 'payments'"):
            asyncio.run(repository.save_versioned(_pinned("billing"), 0))

        assert not any("mcp_server_configs" in c.args[0] for c in cursor.execute.call_args_list)
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()

    def test_a_plain_row_under_a_recorded_name_is_refused(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.fetchall.return_value = [("payments", "lineage")]

        with pytest.raises(PeerKeyNameRefused):
            asyncio.run(
                repository.save_versioned(McpServerConfigSnapshot(mcp_server_id="payments", mode="remote"), None)
            )

        connection.commit.assert_not_called()
