"""Every way a server reaches the fleet checks the peer-key name record (#1947, ADR-030 §9).

The record is kept by the configuration row's write, so it guards only what
writes a row. The configuration file, a reload, `hangar_load` and recovery
write none, and discovery must not merely fail on the write but say why. Each of
them is checked here against a record on a real SQLite file.
"""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from structlog.testing import capture_logs

from mcp_hangar.application.commands.commands import LoadMcpServerCommand
from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.commands.load_handlers import LoadMcpServerHandler
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot, peer_key_name_conflicts
from mcp_hangar.domain.exceptions import ConfigurationError, ConfigurationUnavailableError, PeerKeyNameTaken
from mcp_hangar.domain.model import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.audit_repository import InMemoryAuditRepository
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.in_memory_event_store import InMemoryEventStore
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService
from mcp_hangar.server import config as server_config
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery
from mcp_hangar.server.bootstrap import hot_loading

ENDPOINT = "https://203.0.113.5:8443"


def _tls(key_ref: str) -> dict[str, Any]:
    return {"peer_identity": {"key_ref": key_ref, "spki_sha256": ["sha256:" + "a" * 64]}}


def _pinned(name: str = "payments", key_ref: str = "lineage") -> McpServerConfigSnapshot:
    return McpServerConfigSnapshot(mcp_server_id=name, mode="remote", endpoint=ENDPOINT, tls=_tls(key_ref))


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, *args: Any, **kwargs: Any) -> int:
        return 0


@pytest.fixture
def recorded(tmp_path) -> SQLiteMcpServerConfigRepository:
    """A store whose record holds `payments -> lineage`, and whose row for it was deleted."""
    database = Database(DatabaseConfig(path=str(tmp_path / "fleet.db")))
    repository = SQLiteMcpServerConfigRepository(database)
    asyncio.run(repository.save_versioned(_pinned(), None))
    asyncio.run(repository.delete("payments"))
    return repository


class TestTheFileAgainstTheRecord:
    def test_a_recorded_name_declared_plain_is_a_conflict(self) -> None:
        assert peer_key_name_conflicts({"payments": None}, {"payments": "lineage"})

    def test_a_recorded_name_with_another_key_ref_is_a_conflict(self) -> None:
        assert peer_key_name_conflicts({"payments": "another"}, {"payments": "lineage"})

    def test_a_recorded_key_ref_under_another_name_is_a_conflict(self) -> None:
        assert peer_key_name_conflicts({"billing": "lineage"}, {"payments": "lineage"})

    def test_two_file_entries_with_one_key_ref_conflict_with_no_record_at_all(self) -> None:
        assert peer_key_name_conflicts({"a": "lineage", "b": "lineage"}, {}) == [
            "'b' and 'a' both declare key_ref 'lineage'"
        ]

    def test_a_registered_server_moved_into_the_file_agrees(self) -> None:
        assert peer_key_name_conflicts({"payments": "lineage", "other": None}, {"payments": "lineage"}) == []


@pytest.fixture
def on_the_store(monkeypatch, recorded):
    monkeypatch.setattr(server_config, "get_runtime", lambda: SimpleNamespace(config_repository=recorded))
    return recorded


_PLAIN = {"payments": {"mode": "remote", "endpoint": ENDPOINT}}


class TestLoadAndReload:
    def test_startup_refuses_a_plain_entry_under_a_recorded_name(self, on_the_store) -> None:
        with pytest.raises(ConfigurationError, match="'payments' is in the peer-key name record"):
            server_config.load_config(_PLAIN)

    def test_a_reload_refuses_it_before_anything_is_stopped(self, on_the_store) -> None:
        with pytest.raises(ConfigurationError, match="without a peer identity"):
            server_config.ServerConfigLoader().prepare_mcp_servers(_PLAIN)

    def test_the_same_key_ref_under_the_recorded_name_is_accepted(self, on_the_store) -> None:
        staged = server_config.build_config(
            {"payments": {"mode": "remote", "endpoint": ENDPOINT, "tls": _tls("lineage")}}
        )

        server_config._refuse_what_the_record_contradicts(staged)

    def test_two_entries_sharing_a_key_ref_are_refused_at_startup_and_at_reload(self, on_the_store) -> None:
        twins = {name: {"mode": "remote", "endpoint": ENDPOINT, "tls": _tls("shared")} for name in ("twin-a", "twin-b")}

        with pytest.raises(ConfigurationError, match="both declare key_ref 'shared'"):
            server_config.load_config(twins)
        with pytest.raises(ConfigurationError, match="both declare key_ref 'shared'"):
            server_config.ServerConfigLoader().prepare_mcp_servers(twins)

    def test_a_record_that_cannot_be_read_refuses_the_file(self, monkeypatch) -> None:
        broken = MagicMock()
        broken.get_peer_key_names = AsyncMock(side_effect=OSError("unreachable"))
        monkeypatch.setattr(server_config, "get_runtime", lambda: SimpleNamespace(config_repository=broken))

        with pytest.raises(ConfigurationUnavailableError):
            server_config.load_config(_PLAIN)


class TestRecovery:
    def _recover(self, repository) -> tuple[InMemoryMcpServerRepository, Any]:
        fleet = InMemoryMcpServerRepository()
        service = RecoveryService(
            database=None,
            mcp_server_repository=fleet,
            config_repository=repository,
            audit_repository=InMemoryAuditRepository(),
            event_store=InMemoryEventStore(),
        )
        asyncio.run(service.recover_mcp_servers())
        return fleet, service._last_recovery

    def test_a_plain_row_under_a_recorded_name_is_not_restored(self, recorded) -> None:
        # What an older replica's create leaves: the old upsert, past the record.
        asyncio.run(recorded.save(McpServerConfigSnapshot(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT)))

        with capture_logs() as logs:
            fleet, result = self._recover(recorded)

        assert not fleet.exists("payments")
        assert "payments" in result.failed_ids
        assert any(entry["event"] == "recovery_refused_row_under_peer_key_name" for entry in logs)

    def test_a_row_that_declares_the_recorded_key_is_restored(self, recorded) -> None:
        asyncio.run(recorded.save_versioned(_pinned(), None))

        fleet, _ = self._recover(recorded)

        assert fleet.exists("payments")


class TestHangarLoad:
    def _handler(self, peer_key_names) -> LoadMcpServerHandler:
        repository = MagicMock()
        repository.exists.return_value = False
        store = MagicMock()
        store.exists.return_value = False
        return LoadMcpServerHandler(
            registry_client=MagicMock(),
            package_resolver=MagicMock(),
            secrets_resolver=MagicMock(),
            installers=[],
            runtime_store=store,
            event_bus=_Bus(),
            mcp_server_factory=MagicMock(),
            mcp_server_repository=repository,
            peer_key_names=peer_key_names,
        )

    def test_a_recorded_name_is_not_hot_loaded(self, recorded) -> None:
        handler = self._handler(recorded.get_peer_key_names)
        handler._find_server = AsyncMock()  # type: ignore[method-assign]

        result = asyncio.run(handler.handle(LoadMcpServerCommand(name="payments")))

        assert result.status == "failed"
        assert "peer-key name record" in result.message
        handler._find_server.assert_not_called()

    def test_the_bootstrap_wires_the_record_of_the_runtimes_store(self, monkeypatch, recorded) -> None:
        monkeypatch.setattr(hot_loading, "get_runtime", lambda: MagicMock())
        runtime = MagicMock(config_repository=recorded)

        handler, _ = hot_loading.init_hot_loading(runtime, {})

        assert handler is not None
        assert handler._peer_key_names == recorded.get_peer_key_names


class TestTheCreateCommandAgainstTheFile:
    def test_a_key_ref_a_file_server_holds_is_refused(self, recorded) -> None:
        fleet = InMemoryMcpServerRepository()
        fleet.add("files", McpServer(mcp_server_id="files", mode="remote", endpoint=ENDPOINT, tls=_tls("from-file")))
        writer = RepositoryFleetWriter(recorded)
        allow = SimpleNamespace(may_declare=lambda principal: True)
        try:
            handler = CreateMcpServerHandler(fleet, _Bus(), fleet_writer=writer, peer_identity_authority=allow)
            with pytest.raises(PeerKeyNameTaken, match="already names the server 'files'"):
                handler.handle(
                    CreateMcpServerCommand(
                        mcp_server_id="billing", mode="remote", endpoint=ENDPOINT, tls=_tls("from-file")
                    )
                )
        finally:
            writer.close()


class _Commands:
    def __init__(self, *handlers: Any) -> None:
        self._handlers = handlers

    def send(self, command: Any) -> Any:
        for handler in self._handlers:
            if type(command).__name__.removesuffix("Command") in type(handler).__name__:
                return handler.handle(command)
        raise AssertionError(command)


class TestDiscovery:
    @pytest.fixture
    def gateway(self, monkeypatch, recorded):
        fleet = InMemoryMcpServerRepository()
        writer = RepositoryFleetWriter(recorded)
        allow = SimpleNamespace(may_declare=lambda principal: True)
        handlers = {"repository": fleet, "event_bus": _Bus(), "fleet_writer": writer}
        commands = _Commands(
            CreateMcpServerHandler(**handlers, peer_identity_authority=allow),
            DeleteMcpServerHandler(**handlers, peer_identity_authority=allow),
        )
        monkeypatch.setattr(
            bootstrap_discovery, "get_runtime", lambda: SimpleNamespace(repository=fleet, command_bus=commands)
        )
        yield fleet
        writer.close()

    @pytest.mark.asyncio
    async def test_a_recorded_name_is_not_registered_and_says_why(self, gateway) -> None:
        found = SimpleNamespace(
            name="payments", mode="http", source_type="kubernetes", connection_info={"endpoint": ENDPOINT}, metadata={}
        )

        with capture_logs() as logs:
            assert await bootstrap_discovery._on_mcp_server_register(found) is False

        assert not gateway.exists("payments")
        assert [entry["log_level"] for entry in logs if entry["event"] == "discovered_peer_key_upstream"] == ["warning"]

    @pytest.mark.asyncio
    async def test_deleting_a_server_moved_to_a_key_pinned_registration_is_an_info_line(self, gateway) -> None:
        gateway.add(
            "payments", McpServer(mcp_server_id="payments", mode="remote", endpoint=ENDPOINT, tls=_tls("lineage"))
        )

        with capture_logs() as logs:
            await bootstrap_discovery._on_mcp_server_deregister("payments", "pod_gone")

        assert gateway.exists("payments")
        events = {entry["event"]: entry["log_level"] for entry in logs}
        assert events.get("discovery_deregistration_refused_peer_key") == "info"
        assert "discovery_deregistration_failed" not in events
