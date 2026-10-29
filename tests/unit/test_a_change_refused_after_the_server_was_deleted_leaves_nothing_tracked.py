"""A change refused after its server was deleted leaves nothing tracked (#2143).

A discovered server deleted over the API is gone from the fleet while
discovery still tracks it. When its source then reports a changed definition,
the update goes in as a new registration; refused (an endpoint the SSRF guard
blocks, say), the update callback re-raised the error as a transient one. The
orchestrator kept tracking the definition that used to run, so tracking and
the repository disagreed: nothing ran under the name, and its next unchanged
report was skipped as already registered.

The callback now says the server is gone, and the orchestrator stops tracking
it, so the next report registers it as a new server.

These drive the real service, orchestrator and lifecycle manager, the real
bootstrap callbacks and the real create and delete handlers, recording to a
SQLite fleet store, with a clock the test moves.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

TTL = 20
#: A changed definition the create handler refuses with a ValueError (SsrfBlocked).
BLOCKED = ("http", {"endpoint": "http://169.254.169.254/mcp"})
#: A changed definition the bootstrap cannot build a command from: a container with no image.
UNBUILDABLE = ("container", {"command": ["serve"]})


class _Clock:
    now = datetime(2026, 1, 1, tzinfo=UTC)


class _ClockedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _Clock.now


class _Bus:
    def publish(self, event: Any) -> None:
        pass

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        return len(events) - 1


class _CommandBus:
    """The real create and delete handlers, recording to the fleet store."""

    def __init__(self, repository, writer) -> None:
        common = {"repository": repository, "event_bus": _Bus(), "fleet_writer": writer}
        self._handlers = {
            CreateMcpServerCommand: CreateMcpServerHandler(**common),
            DeleteMcpServerCommand: DeleteMcpServerHandler(**common),
        }

    def send(self, command: Any) -> Any:
        return self._handlers[type(command)].handle(command)


class _Source(DiscoverySource):
    """Reports `defs`: name -> (mode, connection_info)."""

    def __init__(self) -> None:
        super().__init__(mode=DiscoveryMode.ADDITIVE)
        self.defs: dict[str, tuple[str, dict[str, Any]]] = {}

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
        return [
            DiscoveredMcpServer.create(
                name=name, source_type="filesystem", mode=mode, connection_info=conn, metadata={}, ttl_seconds=TTL
            )
            for name, (mode, conn) in sorted(self.defs.items())
        ]

    async def health_check(self) -> bool:
        return True


def _subprocess(module: str) -> tuple[str, dict[str, Any]]:
    return "subprocess", {"command": ["python", "-m", module]}


@pytest.fixture
def fleet(monkeypatch, tmp_path) -> Iterator[SimpleNamespace]:
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)

    repo = InMemoryMcpServerRepository()
    writer = RepositoryFleetWriter(
        SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    )
    commands = _CommandBus(repo, writer)
    monkeypatch.setattr(
        bootstrap_discovery,
        "get_runtime",
        lambda: SimpleNamespace(repository=repo, command_bus=commands, event_bus=_Bus()),
    )

    def build(**config_kwargs: Any) -> tuple[DiscoveryOrchestrator, _Source]:
        source = _Source()
        config = DiscoveryConfig(default_ttl_s=TTL, **config_kwargs)
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config)
        # As create_discovery_orchestrator wires them.
        orchestrator.on_register = bootstrap_discovery._on_mcp_server_register
        orchestrator.on_update = bootstrap_discovery._on_mcp_server_update
        orchestrator.on_deregister = bootstrap_discovery._on_mcp_server_deregister
        # Wired by start(), which would also start the background loops.
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        orchestrator.add_source(source)
        return orchestrator, source

    yield SimpleNamespace(repo=repo, commands=commands, build=build)
    writer.close()


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int = 5) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


def _tracked(orchestrator: DiscoveryOrchestrator) -> DiscoveredMcpServer | None:
    return orchestrator._lifecycle_manager.get_mcp_server("svc")


def _command_of(repo, name: str = "svc") -> list[str] | None:
    server = repo.get(name)
    return None if server is None else list(server._command)


@pytest.mark.asyncio
class TestARefusedChangeOfADeletedServer:
    @pytest.mark.parametrize(
        "change",
        [
            pytest.param(BLOCKED, id="refused-by-the-handler"),
            pytest.param(UNBUILDABLE, id="no-command-can-be-built"),
        ],
    )
    async def test_tracking_and_the_repository_agree(self, fleet, change) -> None:
        orchestrator, source = fleet.build()
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))

        source.defs["svc"] = change
        await _tick(orchestrator)

        assert fleet.repo.exists("svc") is False
        assert _tracked(orchestrator) is None
        assert orchestrator._validator._mcp_server_counts == {"filesystem": 0}

    async def test_the_next_unchanged_report_registers_it_again(self, fleet) -> None:
        orchestrator, source = fleet.build()
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))
        source.defs["svc"] = BLOCKED
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        assert _command_of(fleet.repo) == ["python", "-m", "old"]
        assert _tracked(orchestrator).connection_info["command"] == ["python", "-m", "old"]

    async def test_a_fixed_change_is_registered_as_a_new_server(self, fleet) -> None:
        orchestrator, source = fleet.build()
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))
        source.defs["svc"] = BLOCKED
        await _tick(orchestrator)
        # Refused again as a new registration: still nothing on either side.
        await _tick(orchestrator)
        assert (fleet.repo.exists("svc"), _tracked(orchestrator)) == (False, None)

        source.defs["svc"] = _subprocess("new")
        await _tick(orchestrator)

        assert _command_of(fleet.repo) == ["python", "-m", "new"]
        assert _tracked(orchestrator).connection_info["command"] == ["python", "-m", "new"]

    async def test_a_server_still_running_keeps_running_and_tracked(self, fleet) -> None:
        orchestrator, source = fleet.build()
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = BLOCKED
        await _tick(orchestrator)

        assert _command_of(fleet.repo) == ["python", "-m", "old"]
        assert _tracked(orchestrator).connection_info["command"] == ["python", "-m", "old"]


@pytest.mark.asyncio
class TestARefusedApprovalOfAChangeOfADeletedServer:
    async def _waiting_change_of_a_deleted_server(self, fleet, where: str) -> tuple[DiscoveryOrchestrator, _Source]:
        if where == "pending":
            orchestrator, source = fleet.build(auto_register=False)
            source.defs["svc"] = _subprocess("old")
            await _tick(orchestrator)
            await orchestrator.approve_mcp_server("svc")
            source.defs["svc"] = BLOCKED
            await _tick(orchestrator)
            assert [p.name for p in orchestrator.get_pending_mcp_servers()] == ["svc"]
        else:
            orchestrator, source = fleet.build()
            source.defs["svc"] = _subprocess("old")
            await _tick(orchestrator)
            # Quarantined by a limit, which an approval overrides.
            orchestrator._validator.config.max_registration_rate = 0
            source.defs["svc"] = BLOCKED
            await _tick(orchestrator)
            assert "svc" in orchestrator.get_quarantined()
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))
        return orchestrator, source

    def _waiting(self, orchestrator: DiscoveryOrchestrator, where: str) -> list[str]:
        if where == "pending":
            return [p.name for p in orchestrator.get_pending_mcp_servers()]
        return list(orchestrator.get_quarantined())

    @pytest.mark.parametrize("where", ["pending", "quarantine"])
    async def test_the_change_waits_and_nothing_is_tracked(self, fleet, where) -> None:
        orchestrator, _ = await self._waiting_change_of_a_deleted_server(fleet, where)

        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is False
        assert self._waiting(orchestrator, where) == ["svc"]
        assert fleet.repo.exists("svc") is False
        assert _tracked(orchestrator) is None
        assert orchestrator._validator._mcp_server_counts == {"filesystem": 0}

        await _tick(orchestrator)
        assert self._waiting(orchestrator, where) == ["svc"]
        assert (fleet.repo.exists("svc"), _tracked(orchestrator)) == (False, None)

    async def test_the_old_definition_is_not_registered_again_unapproved(self, fleet) -> None:
        orchestrator, source = await self._waiting_change_of_a_deleted_server(fleet, "pending")
        await orchestrator.approve_mcp_server("svc")

        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        assert fleet.repo.exists("svc") is False
        assert [p.connection_info for p in orchestrator.get_pending_mcp_servers()] == [_subprocess("old")[1]]

        await orchestrator.approve_mcp_server("svc")
        assert _command_of(fleet.repo) == ["python", "-m", "old"]
        assert orchestrator._validator._mcp_server_counts == {"filesystem": 1}
