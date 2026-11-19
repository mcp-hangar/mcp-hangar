"""A discovered server the file took over and a reload dropped is discovery's again, as after a restart (#2222).

The reload removed the server from the fleet and told discovery nothing.
Discovery still held its name: tracked, when the takeover was a reload in the
same process, so every cycle took the source's report as unchanged and skipped
it; or static, when the file declared it at boot. A restart on the same file
builds both from scratch, and the source's report registered it again. Both
paths now end the same way.

These drive the orchestrator `create_discovery_orchestrator` builds, the real
bootstrap callbacks and create handler, and the subscription bootstrap makes on
a real event bus. What the reload itself does to the fleet is stood in for: it
replaces the server with the file's, then removes it, and publishes
`ConfigurationReloaded` with the id it removed.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.events import ConfigurationReloaded
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

TTL = 20
NAME = "found-one"
#: A server the file declares throughout, which no reload removes.
KEPT = "kept"


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
    def __init__(self, repository) -> None:
        self.creates: list[str] = []
        self._create = CreateMcpServerHandler(repository=repository, event_bus=_Bus())

    def send(self, command: Any) -> Any:
        assert isinstance(command, CreateMcpServerCommand), command
        self.creates.append(command.mcp_server_id)
        return self._create.handle(command)


class _Source(DiscoverySource):
    def __init__(self) -> None:
        super().__init__(mode=DiscoveryMode.AUTHORITATIVE)
        self.names: set[str] = set()

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
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
        return True


def _files(name: str) -> McpServer:
    """The server the file builds under *name*: an operator's."""
    return McpServer(mcp_server_id=name, mode="subprocess", command=["python", "-m", name, "--from-the-file"])


class _Gateway:
    """One run of the gateway on the shared fleet: the file's servers, discovery, and the reload subscription."""

    def __init__(self, repo, monkeypatch, declared: tuple[str, ...], auto_register: bool) -> None:
        self.repo = repo
        for name in declared:
            repo.add(name, _files(name))
        self.kept = repo.get(KEPT)
        self.commands = _CommandBus(repo)
        monkeypatch.setattr(
            bootstrap_discovery,
            "get_runtime",
            lambda: SimpleNamespace(repository=repo, command_bus=self.commands, event_bus=_Bus()),
        )
        monkeypatch.setattr(bootstrap_discovery, "set_discovery_orchestrator", lambda _o: None)
        orchestrator = bootstrap_discovery.create_discovery_orchestrator(
            {
                "discovery": {
                    "enabled": True,
                    "auto_register": auto_register,
                    "lifecycle": {"default_ttl_s": TTL},
                    "security": {"require_health_check": False},
                }
            }
        )
        assert orchestrator is not None
        self.orchestrator: DiscoveryOrchestrator = orchestrator
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        self.source = _Source()
        orchestrator.add_source(self.source)
        self.events = EventBus()
        bootstrap_discovery.forget_what_a_reload_removes(self.events, repo, orchestrator)

    async def tick(self, seconds: int = 5) -> None:
        _Clock.now += timedelta(seconds=seconds)
        await self.orchestrator.run_discovery_cycle()
        await self.orchestrator._lifecycle_manager._check_expirations()

    def reload_removed(self, *removed: str) -> None:
        """What the reload does once the file no longer declares *removed*."""
        for name in removed:
            self.repo.remove(name)
        self.announce(*removed)

    def announce(self, *removed: str) -> None:
        """`ConfigurationReloaded`, as the reload publishes it once it has applied."""
        self.events.publish(
            ConfigurationReloaded(
                config_path="config.yaml",
                mcp_servers_added=[],
                mcp_servers_removed=list(removed),
                mcp_servers_updated=[],
                mcp_servers_unchanged=[KEPT],
                reload_duration_ms=1.0,
                requested_by="test",
            )
        )

    def outcome(self) -> dict[str, Any]:
        server = self.repo.get(NAME)
        return {
            "provenance": None if server is None else server.provenance,
            "tracked": self.orchestrator.is_tracking(NAME),
            "pending": [s.name for s in self.orchestrator.get_pending_mcp_servers()],
            "kept_is_the_files": self.repo.get(KEPT) is self.kept,
        }


@pytest.fixture
def boot(monkeypatch) -> Iterator[Callable[..., _Gateway]]:
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)
    repo = InMemoryMcpServerRepository()

    def boot(*declared: str, auto_register: bool = True) -> _Gateway:
        return _Gateway(repo, monkeypatch, (KEPT, *declared), auto_register)

    yield boot


async def _taken_over_by_a_reload(boot, *, reported_after: bool, auto_register: bool = True) -> _Gateway:
    """Discovery registers it; a reload takes it over; a later reload drops it. One process."""
    gateway = boot(auto_register=auto_register)
    gateway.source.names = {NAME, KEPT}
    await gateway.tick()
    if not auto_register:
        await gateway.orchestrator.approve_mcp_server(NAME)
    assert gateway.repo.get(NAME).provenance is Provenance.DISCOVERY
    gateway.repo.add(NAME, _files(NAME))
    await gateway.tick()
    assert gateway.repo.get(NAME).provenance is Provenance.HUMAN
    if not reported_after:
        gateway.source.names = {KEPT}
    gateway.reload_removed(NAME)
    return gateway


async def _declared_at_boot(boot, *, reported_after: bool, auto_register: bool = True) -> _Gateway:
    """The file declared it when this process booted; a reload drops it."""
    gateway = boot(NAME, auto_register=auto_register)
    gateway.source.names = {NAME, KEPT}
    await gateway.tick()
    assert gateway.repo.get(NAME).provenance is Provenance.HUMAN
    if not reported_after:
        gateway.source.names = {KEPT}
    gateway.reload_removed(NAME)
    return gateway


async def _restarted(boot, *, reported_after: bool, auto_register: bool = True) -> _Gateway:
    """A restart on the file that dropped it: discovery starts from nothing."""
    gateway = boot(auto_register=auto_register)
    gateway.source.names = {NAME, KEPT} if reported_after else {KEPT}
    return gateway


PATHS = {
    "taken_over_by_a_reload": _taken_over_by_a_reload,
    "declared_at_boot": _declared_at_boot,
    "restart": _restarted,
}


async def _after(boot, path: str, *, reported_after: bool, auto_register: bool = True) -> dict[str, Any]:
    gateway = await PATHS[path](boot, reported_after=reported_after, auto_register=auto_register)
    for _ in range(TTL // 5 + 2):
        await gateway.tick()
    return gateway.outcome()


@pytest.mark.asyncio
class TestEveryPathEndsTheSameWay:
    @pytest.mark.parametrize("path", PATHS)
    async def test_one_its_source_still_reports_is_discoverys_again(self, boot, path: str) -> None:
        assert await _after(boot, path, reported_after=True) == {
            "provenance": Provenance.DISCOVERY,
            "tracked": True,
            "pending": [],
            "kept_is_the_files": True,
        }

    @pytest.mark.parametrize("path", PATHS)
    async def test_without_auto_register_it_waits_for_approval(self, boot, path: str) -> None:
        assert await _after(boot, path, reported_after=True, auto_register=False) == {
            "provenance": None,
            "tracked": False,
            "pending": [NAME],
            "kept_is_the_files": True,
        }

    @pytest.mark.parametrize("path", PATHS)
    async def test_one_its_source_no_longer_reports_stays_gone(self, boot, path: str) -> None:
        assert await _after(boot, path, reported_after=False) == {
            "provenance": None,
            "tracked": False,
            "pending": [],
            "kept_is_the_files": True,
        }


@pytest.mark.asyncio
class TestWhatAReloadDidNotRemoveIsLeftAlone:
    async def test_a_server_the_file_still_declares_stays_the_files(self, boot) -> None:
        gateway = await _declared_at_boot(boot, reported_after=True)
        await gateway.tick()
        assert gateway.repo.get(KEPT) is gateway.kept
        assert KEPT in gateway.orchestrator._conflict_resolver.static_mcp_servers
        assert KEPT not in gateway.commands.creates

    async def test_one_created_again_before_discovery_forgets_it_stays_protected(self, boot) -> None:
        gateway = boot(NAME)
        gateway.source.names = {NAME, KEPT}
        await gateway.tick()
        # Removed by the reload and created again over the API before discovery hears of it.
        gateway.repo.remove(NAME)
        gateway.repo.add(NAME, _files(NAME))
        operators = gateway.repo.get(NAME)
        gateway.announce(NAME)
        for _ in range(3):
            await gateway.tick()
        assert gateway.repo.get(NAME) is operators
        assert NAME in gateway.orchestrator._conflict_resolver.static_mcp_servers
        assert NAME not in gateway.commands.creates


def test_a_reload_on_another_thread_hands_the_forgetting_to_discoverys_loop(boot) -> None:
    """The reload runs off discovery's loop and holds the reload lock: it schedules, and does not wait."""
    gateway = boot(NAME)
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    try:
        gateway.orchestrator._loop = loop
        ran_on: list[threading.Thread] = []

        def gone(name: str) -> bool:
            ran_on.append(threading.current_thread())
            return not gateway.repo.exists(name)

        gateway.repo.remove(NAME)
        gateway.orchestrator.forget_removed([NAME], gone=gone)
        asyncio.run_coroutine_threadsafe(asyncio.sleep(0), loop).result(5)
        assert ran_on == [thread]
        assert NAME not in gateway.orchestrator._conflict_resolver.static_mcp_servers
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
