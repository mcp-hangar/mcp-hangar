"""A key-pinned server's `key_ref` changes, and a deleted one's name is forgotten (#1973, ADR-030 §7, §9).

The peer-key name record refused a name claimed under another `key_ref`, so a
lineage could never change, and an entry outlived its server for good. Both are
deliberate acts under `mcp_servers:peer_identity`: the update moves the name's
entry in the row's transaction, and forgetting a deleted server's name lets it
be used again. A row written for a server the configuration file declares claims
nothing: the file's entries are derived from the file.
"""

import asyncio
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, Mock

import pytest

from mcp_hangar.application.commands.crud_commands import (
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    ForgetPeerKeyNameCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    ForgetPeerKeyNameHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot, PeerKeyClaim, PeerKeyNameRefused
from mcp_hangar.domain.exceptions import McpServerNotFoundError, PeerIdentityChangeDenied, PeerKeyNameTaken
from mcp_hangar.domain.model import McpServer
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
OLD, NEW = "sha256:" + "a" * 64, "sha256:" + "b" * 64


def _tls(pin: str, key_ref: str) -> dict[str, Any]:
    return {"peer_identity": {"key_ref": key_ref, "spki_sha256": [pin]}}


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


class _Authority:
    def __init__(self) -> None:
        self.allows = True

    def may_declare(self, principal: Any) -> bool:
        return self.allows


class _Gateway:
    def __init__(self, configs) -> None:
        self.configs = configs
        self.writer = RepositoryFleetWriter(configs)
        self.fleet = InMemoryMcpServerRepository()
        self.authority = _Authority()
        common = {"repository": self.fleet, "event_bus": _Bus(), "fleet_writer": self.writer}
        self.create = CreateMcpServerHandler(**common, peer_identity_authority=self.authority)
        self.update = UpdateMcpServerHandler(**common, peer_identity_authority=self.authority)
        self.delete = DeleteMcpServerHandler(**common, peer_identity_authority=self.authority)
        self.forget = ForgetPeerKeyNameHandler(self.fleet, self.writer, self.authority)

    def register(self, name: str = "payments", key_ref: str = "lineage", pin: str = OLD) -> None:
        self.create.handle(
            CreateMcpServerCommand(mcp_server_id=name, mode="remote", endpoint=ENDPOINT, tls=_tls(pin, key_ref))
        )

    def names(self) -> dict[str, str]:
        return asyncio.run(self.configs.get_peer_key_names())


@pytest.fixture(params=["sqlite", "memory"])
def gateway(request, tmp_path):
    configs = (
        SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
        if request.param == "sqlite"
        else InMemoryMcpServerConfigRepository()
    )
    gateway = _Gateway(configs)
    yield gateway
    gateway.writer.close()


class TestANewLineage:
    def test_the_entry_moves_and_the_list_need_share_nothing(self, gateway) -> None:
        gateway.register()

        gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", tls=_tls(NEW, "rotated")))

        assert gateway.names() == {"payments": "rotated"}
        assert gateway.fleet.get("payments").upstream_identity() == "peer-key:rotated"

    def test_the_old_key_ref_is_free_again(self, gateway) -> None:
        gateway.register()
        gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", tls=_tls(NEW, "rotated")))

        gateway.register("billing", "lineage")

        assert gateway.names() == {"payments": "rotated", "billing": "lineage"}

    def test_a_key_ref_another_name_holds_is_refused_and_nothing_moves(self, gateway) -> None:
        gateway.register()
        gateway.register("billing", "taken", NEW)

        with pytest.raises(PeerKeyNameTaken, match="already names the server 'billing'"):
            gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", tls=_tls(OLD, "taken")))

        assert gateway.names() == {"payments": "lineage", "billing": "taken"}
        assert asyncio.run(gateway.configs.get("payments")).tls == _tls(OLD, "lineage")

    def test_it_needs_the_permission(self, gateway) -> None:
        gateway.register()
        gateway.authority.allows = False

        with pytest.raises(PeerIdentityChangeDenied):
            gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="payments", tls=_tls(NEW, "rotated")))

        assert gateway.names() == {"payments": "lineage"}


class TestForgettingAName:
    def _deleted(self, gateway) -> None:
        gateway.register()
        gateway.delete.handle(DeleteMcpServerCommand(mcp_server_id="payments"))

    def test_a_forgotten_name_can_be_registered_plain(self, gateway) -> None:
        self._deleted(gateway)

        gateway.forget.handle(ForgetPeerKeyNameCommand(mcp_server_id="payments"))
        gateway.create.handle(CreateMcpServerCommand(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT))

        assert gateway.names() == {}
        assert asyncio.run(gateway.configs.get("payments")).tls is None

    def test_it_needs_the_permission(self, gateway) -> None:
        self._deleted(gateway)
        gateway.authority.allows = False

        with pytest.raises(PeerIdentityChangeDenied):
            gateway.forget.handle(ForgetPeerKeyNameCommand(mcp_server_id="payments"))

        assert gateway.names() == {"payments": "lineage"}

    def test_a_registered_server_is_deleted_first(self, gateway) -> None:
        gateway.register()

        with pytest.raises(PeerKeyNameTaken, match="delete the server"):
            gateway.forget.handle(ForgetPeerKeyNameCommand(mcp_server_id="payments"))

    def test_a_server_a_peer_still_holds_is_refused_by_the_store(self, gateway) -> None:
        # Registered on another replica: absent from this fleet, live in the row.
        gateway.register()
        gateway.fleet.remove("payments")

        with pytest.raises(PeerKeyNameTaken, match="still registered"):
            gateway.forget.handle(ForgetPeerKeyNameCommand(mcp_server_id="payments"))

        assert gateway.names() == {"payments": "lineage"}

    def test_an_unrecorded_name_is_not_found(self, gateway) -> None:
        with pytest.raises(McpServerNotFoundError):
            gateway.forget.handle(ForgetPeerKeyNameCommand(mcp_server_id="nobody"))


def test_a_file_servers_row_claims_nothing(tmp_path) -> None:
    gateway = _Gateway(SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))))
    try:
        gateway.fleet.add(
            "files", McpServer(mcp_server_id="files", mode="remote", endpoint=ENDPOINT, tls=_tls(OLD, "from-file"))
        )

        gateway.update.handle(UpdateMcpServerCommand(mcp_server_id="files", description="renamed"))

        assert asyncio.run(gateway.configs.get("files")).description == "renamed"
        assert gateway.names() == {}
    finally:
        gateway.writer.close()


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


class TestPostgres:
    def test_a_move_deletes_the_names_entry_before_claiming_in_the_rows_transaction(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.fetchall.return_value = [("payments", "rotated")]
        cursor.rowcount = 1
        row = McpServerConfigSnapshot(
            mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=_tls(NEW, "rotated")
        )

        asyncio.run(repository.save_versioned(row, 3, PeerKeyClaim.MOVE))

        statements = [c.args[0] for c in cursor.execute.call_args_list]
        assert statements[0].startswith("DELETE FROM peer_key_names")
        assert "ON CONFLICT DO NOTHING" in statements[1]
        connection.commit.assert_called_once()

    def test_forgetting_a_live_servers_name_rolls_back(self) -> None:
        repository, connection, cursor = _postgres()
        cursor.fetchone.return_value = (1,)

        with pytest.raises(PeerKeyNameRefused):
            asyncio.run(repository.forget_peer_key_name("payments"))

        assert not any("DELETE" in c.args[0] for c in cursor.execute.call_args_list)
        connection.rollback.assert_called_once()
