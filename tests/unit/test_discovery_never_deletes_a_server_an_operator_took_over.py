"""Discovery deletes only a server discovery registered (#2113).

A discovered server re-pointed over the API, or deleted and created again over
the API under its name, carries HUMAN provenance from then on. Discovery still
tracked the name; when its source stopped reporting it, the TTL ran out and
discovery deleted the operator's server. The delete handler now refuses a
deletion discovery starts for a server that is not discovery's, the callback
logs it once, and the lifecycle manager stops tracking the name.

These drive the real service, orchestrator and lifecycle manager, the real
bootstrap callbacks and the real create, update and delete handlers, recording
to a SQLite fleet store, with a clock the test moves.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
import structlog

from mcp_hangar.application.commands.crud_commands import (
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.exceptions import McpServerDeclaredInConfigError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.persistence import Database, DatabaseConfig
from mcp_hangar.infrastructure.persistence.config_repository import SQLiteMcpServerConfigRepository
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

TTL = 20
#: Where an operator re-points the server: a literal address, so the policy a
#: human endpoint is held to needs no DNS.
MOVED = "https://203.0.113.9:8443"


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
    """The real CRUD handlers, recording to the fleet store."""

    def __init__(self, repository, writer, declared: frozenset[str]) -> None:
        self.sent: list[Any] = []
        common = {"repository": repository, "event_bus": _Bus(), "fleet_writer": writer}
        self._handlers = {
            CreateMcpServerCommand: CreateMcpServerHandler(**common),
            UpdateMcpServerCommand: UpdateMcpServerHandler(**common),
            DeleteMcpServerCommand: DeleteMcpServerHandler(**common, declared_in_config=declared.__contains__),
        }

    def send(self, command: Any) -> Any:
        self.sent.append(command)
        return self._handlers[type(command)].handle(command)

    def deletes(self, provenance: Provenance) -> list[DeleteMcpServerCommand]:
        return [c for c in self.sent if isinstance(c, DeleteMcpServerCommand) and c.provenance is provenance]


class _Source(DiscoverySource):
    """Reports `defs`: name -> (mode, connection_info, metadata)."""

    def __init__(self) -> None:
        # Authoritative: what it stops reporting is removed one TTL later. An
        # additive source never removes anything, so would prove nothing here.
        super().__init__(mode=DiscoveryMode.AUTHORITATIVE)
        self.defs: dict[str, tuple[str, dict[str, Any], dict[str, Any]]] = {}

    @property
    def source_type(self) -> str:
        return "docker"

    async def discover(self) -> list[DiscoveredMcpServer]:
        return [
            DiscoveredMcpServer.create(
                name=name, source_type="docker", mode=mode, connection_info=conn, metadata=meta, ttl_seconds=TTL
            )
            for name, (mode, conn, meta) in sorted(self.defs.items())
        ]

    async def health_check(self) -> bool:
        return True


def _subprocess(module: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    return "subprocess", {"command": ["python", "-m", module]}, {}


def _remote(address: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    # The runtime reported this address for the container, which is what lets
    # a discovered endpoint on a private address through.
    return "http", {"endpoint": f"http://{address}:8080/mcp"}, {"runtime_addresses": [address]}


@pytest.fixture
def fleet(monkeypatch, tmp_path) -> Iterator[SimpleNamespace]:
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)

    repo = InMemoryMcpServerRepository()
    writer = RepositoryFleetWriter(
        SQLiteMcpServerConfigRepository(Database(DatabaseConfig(path=str(tmp_path / "fleet.db"))))
    )
    commands = _CommandBus(repo, writer, declared=frozenset({"declared"}))
    monkeypatch.setattr(
        bootstrap_discovery,
        "get_runtime",
        lambda: SimpleNamespace(repository=repo, command_bus=commands, event_bus=_Bus()),
    )

    source = _Source()
    config = DiscoveryConfig(default_ttl_s=TTL)
    config.security.require_health_check = False
    orchestrator = DiscoveryOrchestrator(config=config, static_mcp_servers={"declared"})
    # As create_discovery_orchestrator wires them.
    orchestrator.on_register = bootstrap_discovery._on_mcp_server_register
    orchestrator.on_update = bootstrap_discovery._on_mcp_server_update
    orchestrator.on_deregister = bootstrap_discovery._on_mcp_server_deregister
    # Wired by start(), which would also start the background loops.
    orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
    orchestrator.add_source(source)

    yield SimpleNamespace(repo=repo, commands=commands, source=source, orchestrator=orchestrator)
    writer.close()


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int = 5) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


async def _lose_and_outlive_the_ttl(fleet, name: str) -> list[dict]:
    """The source stops reporting *name*; run well past its TTL, capturing the log."""
    del fleet.source.defs[name]
    with structlog.testing.capture_logs() as captured:
        for _ in range(8):
            await _tick(fleet.orchestrator, 10)
    return captured


def _events(captured: list[dict], name: str) -> list[dict]:
    return [e for e in captured if e["event"] == name]


@pytest.mark.asyncio
class TestAServerAnOperatorTookOverStays:
    async def test_a_re_pointed_server_survives_its_ttl_and_is_no_longer_tracked(self, fleet) -> None:
        fleet.source.defs["svc"] = _remote("10.0.0.9")
        await _tick(fleet.orchestrator)
        assert fleet.repo.get("svc").provenance is Provenance.DISCOVERY

        # Re-pointed over the API: the endpoint, and the server, are an operator's now.
        fleet.commands.send(UpdateMcpServerCommand(mcp_server_id="svc", endpoint=MOVED))
        await _tick(fleet.orchestrator)
        assert fleet.repo.get("svc").provenance is Provenance.HUMAN

        captured = await _lose_and_outlive_the_ttl(fleet, "svc")

        assert fleet.repo.get("svc")._endpoint == MOVED
        assert not fleet.orchestrator.is_tracking("svc")
        # Asked once, refused once, and never asked again.
        assert len(fleet.commands.deletes(Provenance.DISCOVERY)) == 1
        assert len(_events(captured, "discovery_deregistration_refused_not_discovered")) == 1
        assert not _events(captured, "discovery_deregistration_failed")
        assert not _events(captured, "discovery_deregistered_mcp_server")

    async def test_a_server_re_created_over_the_api_survives_its_ttl(self, fleet) -> None:
        fleet.source.defs["svc"] = _subprocess("discovered")
        await _tick(fleet.orchestrator)

        # Deleted and created again over the API under the same name, while
        # discovery still tracks the name.
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))
        fleet.commands.send(CreateMcpServerCommand(mcp_server_id="svc", mode="subprocess", command=["mine"]))

        captured = await _lose_and_outlive_the_ttl(fleet, "svc")

        assert list(fleet.repo.get("svc")._command) == ["mine"]
        assert not fleet.orchestrator.is_tracking("svc")
        assert len(_events(captured, "discovery_deregistration_refused_not_discovered")) == 1
        assert not _events(captured, "discovery_deregistration_failed")


@pytest.mark.asyncio
class TestWhatDiscoveryStillRemoves:
    async def test_a_discovered_server_is_removed_after_its_ttl(self, fleet) -> None:
        fleet.source.defs["svc"] = _subprocess("discovered")
        await _tick(fleet.orchestrator)

        captured = await _lose_and_outlive_the_ttl(fleet, "svc")

        assert fleet.repo.get("svc") is None
        assert not fleet.orchestrator.is_tracking("svc")
        assert len(_events(captured, "discovery_deregistered_mcp_server")) == 1
        assert not _events(captured, "discovery_deregistration_refused_not_discovered")


class TestTheDeleteHandler:
    """Only a deletion discovery starts is held to the check."""

    def test_an_api_delete_removes_a_discovered_server(self, fleet) -> None:
        fleet.commands.send(
            CreateMcpServerCommand(
                mcp_server_id="svc", mode="subprocess", command=["python"], provenance=Provenance.DISCOVERY
            )
        )

        result = fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))

        assert result == {"mcp_server_id": "svc", "deleted": True}
        assert fleet.repo.get("svc") is None

    @pytest.mark.parametrize("provenance", [Provenance.HUMAN, Provenance.DISCOVERY])
    def test_a_declared_server_is_refused_as_before(self, fleet, provenance) -> None:
        fleet.repo.add("declared", _declared())

        with pytest.raises(McpServerDeclaredInConfigError):
            fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="declared", provenance=provenance))

        assert fleet.repo.get("declared") is not None


def _declared() -> McpServer:
    # The configuration file builds its servers directly, with the default provenance.
    return McpServer(mcp_server_id="declared", mode="subprocess", command=["python", "-m", "declared"])
