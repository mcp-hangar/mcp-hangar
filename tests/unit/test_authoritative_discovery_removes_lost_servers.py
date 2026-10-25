"""An authoritative source removes what it stopped reporting, after its TTL.

It never did. The lost name was looked at once, in the first cycle after it
went -- before its TTL could have run out -- and then dropped from tracking; and
every cycle the orchestrator refreshed the lifecycle TTL of every server the
resolver still held, including the lost one. A deleted container, pod or file
stayed registered until the gateway restarted.

These drive the real service, orchestrator and lifecycle manager, the real
bootstrap callbacks and the real create/delete handlers over an in-memory
repository -- the one the REST listing reads -- with a clock the test moves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.application.queries.handlers import ListMcpServersHandler
from mcp_hangar.application.queries.queries import ListMcpServersQuery
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.events import McpServerDiscoveryLost
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

TTL = 20


class _Clock:
    now = datetime(2026, 1, 1, tzinfo=UTC)


class _ClockedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _Clock.now


class _RecordingBus:
    def __init__(self) -> None:
        self.published: list[Any] = []

    def publish(self, event: Any) -> None:
        self.published.append(event)

    def publish_aggregate_events(self, _type: str, _id: str, events: list) -> int:
        self.published.extend(events)
        return len(events) - 1


class _CommandBus:
    """Runs the real create and delete handlers, and remembers what it ran."""

    def __init__(self, repository, event_bus) -> None:
        self.sent: list[Any] = []
        self._create = CreateMcpServerHandler(repository=repository, event_bus=event_bus)
        self._delete = DeleteMcpServerHandler(repository=repository, event_bus=event_bus)

    def send(self, command: Any) -> Any:
        self.sent.append(command)
        if isinstance(command, CreateMcpServerCommand):
            return self._create.handle(command)
        return self._delete.handle(command)

    def of(self, kind: type) -> list[Any]:
        return [c for c in self.sent if isinstance(c, kind)]


class _Source(DiscoverySource):
    def __init__(self, mode: DiscoveryMode) -> None:
        super().__init__(mode=mode)
        self.names: set[str] = set()
        self.fail = False
        #: How Docker and a missing filesystem path fail: an empty answer, and
        #: health_check() reporting the source could not reach what it watches.
        self.unreachable = False

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
        if self.fail:
            raise RuntimeError("source unreachable")
        if self.unreachable:
            return []
        return [
            DiscoveredMcpServer.create(
                name=name,
                source_type="filesystem",
                mode="subprocess",
                connection_info={"command": ["python", "-m", name]},
                metadata={},
                ttl_seconds=TTL,
            )
            for name in sorted(self.names)
        ]

    async def health_check(self) -> bool:
        return not self.unreachable


@pytest.fixture
def fleet(monkeypatch):
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)

    repo = InMemoryMcpServerRepository()
    events = _RecordingBus()
    commands = _CommandBus(repo, events)
    monkeypatch.setattr(
        bootstrap_discovery,
        "get_runtime",
        lambda: SimpleNamespace(repository=repo, command_bus=commands, event_bus=events),
    )

    def build(mode: DiscoveryMode, static: set[str] | None = None):
        config = DiscoveryConfig(default_ttl_s=TTL)
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, static_mcp_servers=static, event_bus=events)
        orchestrator.on_register = bootstrap_discovery._on_mcp_server_register
        orchestrator.on_deregister = bootstrap_discovery._on_mcp_server_deregister
        # Wired by start(), which would also start the background loops.
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        source = _Source(mode)
        orchestrator.add_source(source)
        return orchestrator, source

    return SimpleNamespace(repo=repo, events=events, commands=commands, build=build)


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


def _listed(repo) -> set[str]:
    return {s.mcp_server_id for s in ListMcpServersHandler(repo).handle(ListMcpServersQuery())}


@pytest.mark.asyncio
class TestAuthoritativeRemovesWhatWentAway:
    async def test_a_lost_server_stays_until_its_ttl_runs_out(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"gone"}
        await _tick(orchestrator, 0)
        assert _listed(fleet.repo) == {"gone"}

        source.names = set()
        for _ in range(3):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"gone"}
        assert fleet.commands.of(DeleteMcpServerCommand) == []

    async def test_a_lost_server_is_removed_once_after_its_ttl(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"gone", "kept"}
        await _tick(orchestrator, 0)

        source.names = {"kept"}
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"kept"}
        [delete] = fleet.commands.of(DeleteMcpServerCommand)
        assert delete.mcp_server_id == "gone"
        [lost] = [e for e in fleet.events.published if isinstance(e, McpServerDiscoveryLost)]
        assert lost.mcp_server_name == "gone"
        assert lost.reason == "ttl_expired"
        # The per-source count `max_mcp_servers_per_source` checks came down too.
        assert orchestrator._validator._mcp_server_counts["filesystem"] == 1

    async def test_a_removed_server_is_not_created_again(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"gone"}
        await _tick(orchestrator, 0)

        source.names = set()
        for _ in range(20):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == set()
        assert len(fleet.commands.of(CreateMcpServerCommand)) == 1
        assert len(fleet.commands.of(DeleteMcpServerCommand)) == 1
        assert orchestrator._discovery_service.get_registered_mcp_servers() == {}

    async def test_an_expiry_between_cycles_is_not_undone_by_the_next_cycle(self, fleet) -> None:
        # The expiry check runs on its own interval. When it removes the server
        # and the next cycle's source fails -- so the cycle cannot conclude the
        # name is lost -- the resolver must not still hold an entry the cycle
        # would register as new.
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"gone"}
        await _tick(orchestrator, 0)
        source.names = set()
        await _tick(orchestrator, 5)

        _Clock.now += timedelta(seconds=TTL)
        await orchestrator._lifecycle_manager._check_expirations()
        assert _listed(fleet.repo) == set()

        source.fail = True
        for _ in range(3):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == set()
        assert len(fleet.commands.of(CreateMcpServerCommand)) == 1

    async def test_a_server_that_returns_after_removal_registers_again(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"back"}
        await _tick(orchestrator, 0)
        source.names = set()
        for _ in range(6):
            await _tick(orchestrator, 5)
        assert _listed(fleet.repo) == set()

        source.names = {"back"}
        await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"back"}
        assert len(fleet.commands.of(CreateMcpServerCommand)) == 2

    async def test_a_server_that_returns_before_its_ttl_is_left_running(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"flaky"}
        await _tick(orchestrator, 0)

        source.names = set()
        await _tick(orchestrator, 5)
        await _tick(orchestrator, 5)
        source.names = {"flaky"}
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"flaky"}
        assert len(fleet.commands.of(CreateMcpServerCommand)) == 1
        assert fleet.commands.of(DeleteMcpServerCommand) == []

    async def test_a_source_that_fails_removes_nothing(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"survivor"}
        await _tick(orchestrator, 0)

        source.fail = True
        for _ in range(20):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"survivor"}
        assert fleet.commands.of(DeleteMcpServerCommand) == []

    async def test_a_source_that_answers_empty_while_unreachable_removes_nothing(self, fleet) -> None:
        # Docker on a connection failure and a missing filesystem path answer
        # `[]` instead of raising; that must not read as "everything is gone".
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE)
        source.names = {"survivor"}
        await _tick(orchestrator, 0)

        source.unreachable = True
        for _ in range(20):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"survivor"}
        assert fleet.commands.of(DeleteMcpServerCommand) == []


@pytest.mark.asyncio
class TestOthersAreUntouched:
    async def test_an_additive_source_never_removes(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.ADDITIVE)
        source.names = {"stays"}
        await _tick(orchestrator, 0)

        source.names = set()
        for _ in range(20):
            await _tick(orchestrator, 5)

        assert _listed(fleet.repo) == {"stays"}
        assert fleet.commands.of(DeleteMcpServerCommand) == []

    async def test_a_static_name_is_not_touched(self, fleet) -> None:
        orchestrator, source = fleet.build(DiscoveryMode.AUTHORITATIVE, static={"declared"})
        source.names = {"declared"}
        await _tick(orchestrator, 0)
        source.names = set()
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert fleet.commands.sent == []
