"""A server discovery registered is still discovery's after a restart (#2106).

On a durable backend the fleet is restored before discovery starts, and the
orchestrator seeded "static always wins" from every id in the repository. So
every server discovery had registered came back static: an authoritative source
never removed it again, and nobody had declared the server it now protected.

These go through the real entry points: the create and delete handlers writing
a SQLite backend, `restore_persisted_fleet` reading it back into a fresh
repository, and `create_discovery_orchestrator` building the orchestrator over
it, with a clock the test moves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import (
    AdoptDiscoveredMcpServerCommand,
    CreateMcpServerCommand,
    DeleteMcpServerCommand,
    UpdateMcpServerCommand,
)
from mcp_hangar.application.commands.crud_handlers import (
    AdoptDiscoveredMcpServerHandler,
    CreateMcpServerHandler,
    DeleteMcpServerHandler,
    UpdateMcpServerHandler,
)
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.domain.contracts.persistence import McpServerConfigSnapshot
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.events import McpServerDiscoveryConfigChanged
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import DiscoveryOrigin, Provenance
from mcp_hangar.infrastructure.async_bridge import BackgroundLoop
from mcp_hangar.infrastructure.persistence.fleet_writer import RepositoryFleetWriter
from mcp_hangar.infrastructure.persistence.recovery_service import RecoveryService
from mcp_hangar.infrastructure.persistence.registry import create_backend
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery
from mcp_hangar.server.bootstrap.persistence import restore_persisted_fleet

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
    """The real CRUD handlers, recording to the durable backend."""

    def __init__(self, repository, event_bus, writer) -> None:
        self.sent: list[Any] = []
        self._handlers = {
            CreateMcpServerCommand: CreateMcpServerHandler(
                repository=repository, event_bus=event_bus, fleet_writer=writer
            ),
            DeleteMcpServerCommand: DeleteMcpServerHandler(
                repository=repository, event_bus=event_bus, fleet_writer=writer
            ),
            UpdateMcpServerCommand: UpdateMcpServerHandler(
                repository=repository, event_bus=event_bus, fleet_writer=writer
            ),
            AdoptDiscoveredMcpServerCommand: AdoptDiscoveredMcpServerHandler(
                repository=repository, fleet_writer=writer
            ),
        }

    def send(self, command: Any) -> Any:
        self.sent.append(command)
        return self._handlers[type(command)].handle(command)

    def of(self, kind: type) -> list[Any]:
        return [c for c in self.sent if isinstance(c, kind)]


class _Source(DiscoverySource):
    def __init__(self, mode: DiscoveryMode) -> None:
        super().__init__(mode=mode)
        self.names: set[str] = set()
        self.variant = ""

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
        return [
            DiscoveredMcpServer.create(
                name=name,
                source_type="filesystem",
                mode="subprocess",
                connection_info={"command": ["python", "-m", name + self.variant]},
                metadata={},
                ttl_seconds=TTL,
            )
            for name in sorted(self.names)
        ]

    async def health_check(self) -> bool:
        return True


class _Gateway:
    """One run of the gateway over the shared backend: boot, discovery, and its fleet."""

    def __init__(self, backend, monkeypatch, mode: DiscoveryMode, declared: tuple[str, ...]) -> None:
        #: Whether this instance holds the management lease.
        self.holds_the_lease = True
        monkeypatch.setattr(bootstrap_discovery, "may_manage", lambda: self.holds_the_lease)
        self.repo = InMemoryMcpServerRepository()
        # The configuration file is loaded first, and builds its servers directly.
        for name in declared:
            self.repo.add(name, McpServer(mcp_server_id=name, mode="subprocess", command=["python", "-m", name]))
        self.restored = restore_persisted_fleet(
            SimpleNamespace(
                repository=self.repo,
                recovery_service=RecoveryService(
                    database=None,
                    mcp_server_repository=self.repo,
                    config_repository=backend.config_repository(),
                    audit_repository=backend.audit_repository(),
                ),
                persistence_config=SimpleNamespace(enabled=True, auto_recover=True),
            )
        )
        self.events = _RecordingBus()
        self.writer = RepositoryFleetWriter(backend.config_repository())
        self.commands = _CommandBus(self.repo, self.events, self.writer)
        monkeypatch.setattr(
            bootstrap_discovery,
            "get_runtime",
            lambda: SimpleNamespace(repository=self.repo, command_bus=self.commands, event_bus=self.events),
        )
        monkeypatch.setattr(bootstrap_discovery, "set_discovery_orchestrator", lambda _o: None)
        orchestrator = bootstrap_discovery.create_discovery_orchestrator(
            {
                "discovery": {
                    "enabled": True,
                    "lifecycle": {"default_ttl_s": TTL},
                    "security": {"require_health_check": False},
                }
            }
        )
        assert orchestrator is not None
        self.orchestrator: DiscoveryOrchestrator = orchestrator
        # Wired by start(), which would also start the background loops.
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        self.source = _Source(mode)
        orchestrator.add_source(self.source)

    async def tick(self, seconds: int = 5) -> None:
        """One discovery cycle and one expiry check, `seconds` after the last, gated as the loops are."""
        _Clock.now += timedelta(seconds=seconds)
        if self.orchestrator._holds_the_lease():
            await self.orchestrator.run_discovery_cycle()
        await self.expiry_check()

    async def expiry_check(self) -> None:
        if self.orchestrator._lifecycle_manager._may_manage():
            await self.orchestrator._lifecycle_manager._check_expirations()

    async def run_past_the_ttl(self) -> None:
        for _ in range(TTL // 5 + 2):
            await self.tick()

    def is_static(self, name: str) -> bool:
        return name in self.orchestrator._conflict_resolver.static_mcp_servers


@pytest.fixture
def durable(tmp_path, monkeypatch):
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)
    backend = create_backend("sqlite", {"data_dir": str(tmp_path)})
    writers: list[RepositoryFleetWriter] = []

    def boot(mode: DiscoveryMode = DiscoveryMode.AUTHORITATIVE, declared: tuple[str, ...] = ()) -> _Gateway:
        gateway = _Gateway(backend, monkeypatch, mode, declared)
        writers.append(gateway.writer)
        return gateway

    def save(snapshot: McpServerConfigSnapshot) -> None:
        writer = RepositoryFleetWriter(backend.config_repository())
        writers.append(writer)
        writer.save(snapshot)

    def row(name: str) -> McpServerConfigSnapshot | None:
        # What the next restart restores: a deleted row is disabled, not dropped.
        loop = BackgroundLoop()
        try:
            rows = loop.run(backend.config_repository().get_all(), 15.0)
        finally:
            loop.close()
        return next((r for r in rows if r.mcp_server_id == name), None)

    try:
        yield SimpleNamespace(boot=boot, row=row, save=save)
    finally:
        for writer in writers:
            writer.close()
        backend.close()


async def _discovered_before_the_restart(durable, *names: str) -> None:
    first = durable.boot()
    first.source.names = set(names)
    await first.tick(0)
    assert set(names) <= set(first.repo.get_all_ids())


@pytest.mark.asyncio
class TestARestoredDiscoveredServerIsStillDiscoverys:
    async def test_it_is_not_seeded_as_static(self, durable) -> None:
        await _discovered_before_the_restart(durable, "found")

        second = durable.boot()

        assert second.restored == 1
        assert second.repo.get("found").provenance is Provenance.DISCOVERY
        assert not second.is_static("found")

    async def test_an_authoritative_source_that_no_longer_reports_it_removes_it_after_the_ttl(self, durable) -> None:
        await _discovered_before_the_restart(durable, "gone")

        second = durable.boot()
        await second.run_past_the_ttl()

        assert second.repo.get("gone") is None
        assert durable.row("gone") is None
        [delete] = second.commands.of(DeleteMcpServerCommand)
        assert delete.mcp_server_id == "gone"

    async def test_one_lost_after_the_restart_is_removed_after_the_ttl(self, durable) -> None:
        await _discovered_before_the_restart(durable, "gone", "kept")

        second = durable.boot()
        second.source.names = {"gone", "kept"}
        await second.tick(0)
        second.source.names = {"kept"}
        await second.run_past_the_ttl()

        assert set(second.repo.get_all_ids()) == {"kept"}
        assert durable.row("gone") is None

    async def test_one_still_reported_is_kept_and_not_created_again(self, durable) -> None:
        await _discovered_before_the_restart(durable, "kept")

        second = durable.boot()
        second.source.names = {"kept"}
        await second.run_past_the_ttl()

        assert set(second.repo.get_all_ids()) == {"kept"}
        assert second.commands.sent == []
        assert not [e for e in second.events.published if isinstance(e, McpServerDiscoveryConfigChanged)]

    async def test_a_definition_changed_while_it_was_down_is_seen_as_a_change(self, durable) -> None:
        await _discovered_before_the_restart(durable, "moved")

        second = durable.boot()
        second.source.names = {"moved"}
        second.source.variant = "-v2"
        await second.tick(0)

        [changed] = [e for e in second.events.published if isinstance(e, McpServerDiscoveryConfigChanged)]
        assert changed.mcp_server_name == "moved"

    async def test_its_ttl_runs_from_when_this_instance_takes_the_lease(self, durable) -> None:
        # A replica that boots as a follower, or waits out a crashed
        # predecessor's lease, must not find every discovered server expired
        # the moment it takes over: the expiry check can run before the first
        # cycle has had a chance to see them.
        await _discovered_before_the_restart(durable, "kept", "gone")

        second = durable.boot()
        second.holds_the_lease = False
        second.source.names = {"kept"}
        await second.run_past_the_ttl()

        second.holds_the_lease = True
        _Clock.now += timedelta(seconds=TTL * 3)
        await second.expiry_check()
        assert set(second.repo.get_all_ids()) == {"kept", "gone"}

        await second.run_past_the_ttl()
        assert set(second.repo.get_all_ids()) == {"kept"}
        assert second.commands.of(CreateMcpServerCommand) == []

    async def test_one_registered_by_another_replica_is_taken_back_with_the_lease(self, durable) -> None:
        # Not restored at boot: the previous holder registered it while this
        # instance followed. It carries its origin, so it is tracked the same way.
        second = durable.boot()
        second.holds_the_lease = False
        await second.tick()
        second.repo.add(
            "late",
            McpServer(
                mcp_server_id="late",
                mode="subprocess",
                command=["python", "-m", "late"],
                provenance=Provenance.DISCOVERY,
                discovered_as=DiscoveryOrigin(source_type="filesystem", fingerprint="f00"),
            ),
        )

        second.holds_the_lease = True
        await second.run_past_the_ttl()

        assert second.repo.get("late") is None

    async def test_an_additive_source_keeps_it_when_it_no_longer_reports_it(self, durable) -> None:
        await _discovered_before_the_restart(durable, "kept")

        second = durable.boot(DiscoveryMode.ADDITIVE)
        await second.run_past_the_ttl()

        assert set(second.repo.get_all_ids()) == {"kept"}
        assert durable.row("kept") is not None


@pytest.mark.asyncio
class TestWhatADeclarationProtectsStaysProtected:
    async def test_a_server_declared_in_the_configuration_file_stays_static(self, durable) -> None:
        second = durable.boot(declared=("pinned",))
        assert second.is_static("pinned")

        second.source.names = {"pinned"}
        await second.tick(0)
        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("pinned") is not None
        assert second.commands.sent == []

    async def test_a_server_created_over_the_api_stays_protected_after_a_restart(self, durable) -> None:
        first = durable.boot()
        first.commands.send(CreateMcpServerCommand(mcp_server_id="manual", mode="subprocess", command=["python"]))

        second = durable.boot()
        assert second.is_static("manual")

        second.source.names = {"manual"}
        await second.tick(0)
        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("manual") is not None
        assert second.commands.sent == []

    async def test_a_row_written_before_provenance_was_recorded_stays_protected(self, durable) -> None:
        # What an older version wrote: no provenance key at all. It reads back
        # HUMAN, so it stays protected, as it was.
        legacy = McpServerConfigSnapshot.from_dict({"mcp_server_id": "old", "mode": "subprocess", "command": ["py"]})
        durable.save(legacy)

        second = durable.boot()
        assert second.is_static("old")

        second.source.names = {"old"}
        await second.tick(0)
        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("old") is not None


@pytest.mark.asyncio
class TestADiscoveredRowFromBeforeThisFix:
    """DISCOVERY provenance, but no recorded origin: only the description discovery gave it."""

    @staticmethod
    def _write(durable, name: str, description: str | None = "Discovered from filesystem") -> None:
        durable.save(
            McpServerConfigSnapshot(
                mcp_server_id=name,
                mode="subprocess",
                command=["python", "-m", name],
                description=description,
                provenance=Provenance.DISCOVERY,
            )
        )

    async def test_its_own_source_takes_it_over_and_removes_it_once_lost(self, durable) -> None:
        self._write(durable, "older")

        second = durable.boot()
        assert not second.is_static("older")
        second.source.names = {"older"}
        await second.tick(0)

        # Taken over, not created again: there is nothing to create.
        assert second.commands.of(CreateMcpServerCommand) == []
        assert second.orchestrator.is_tracking("older")

        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("older") is None
        assert durable.row("older") is None

    async def test_the_takeover_records_the_origin_so_the_next_restart_takes_it_back_at_once(self, durable) -> None:
        self._write(durable, "older")
        second = durable.boot()
        second.source.names = {"older"}
        await second.tick(0)

        row = durable.row("older")
        assert row is not None and row.discovered_as is not None
        assert row.discovered_as.source_type == "filesystem"

        # The next restart: the source no longer reports it, and nothing has
        # to report it first for it to be removed.
        third = durable.boot()
        await third.run_past_the_ttl()

        assert third.repo.get("older") is None

    async def test_another_source_cannot_take_it_over(self, durable) -> None:
        # Registered by the docker source; the filesystem source reports the
        # same name. Before #2106 this row was static, and it must not become
        # the filesystem source's to replace or remove.
        self._write(durable, "theirs", description="Discovered from docker")

        second = durable.boot()
        second.source.names = {"theirs"}
        await second.tick(0)
        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("theirs") is not None
        assert not second.orchestrator.is_tracking("theirs")
        row = durable.row("theirs")
        assert row is not None and row.discovered_as is None

    @pytest.mark.parametrize("description", [None, "edited over the API"])
    async def test_one_whose_source_cannot_be_told_stays_protected(self, durable, description) -> None:
        self._write(durable, "unknown", description=description)

        second = durable.boot()
        assert second.is_static("unknown")
        second.source.names = {"unknown"}
        await second.tick(0)
        second.source.names = set()
        await second.run_past_the_ttl()

        assert second.repo.get("unknown") is not None
        assert second.commands.sent == []

    async def test_it_stays_while_its_source_does_not_report_it(self, durable) -> None:
        # Its definition was never recorded, so it cannot be tracked before its
        # source reports it. It stays until then, as on an additive source.
        self._write(durable, "older")

        second = durable.boot()
        await second.run_past_the_ttl()

        assert second.repo.get("older") is not None


@pytest.mark.asyncio
class TestTheRowFollowsTheServer:
    async def test_a_replaced_definition_records_the_new_fingerprint(self, durable) -> None:
        first = durable.boot()
        first.source.names = {"moved"}
        await first.tick(0)
        before = durable.row("moved")
        assert before is not None and before.discovered_as is not None

        first.source.variant = "-v2"
        await first.tick()

        after = durable.row("moved")
        assert after is not None and after.discovered_as is not None
        assert after.discovered_as.fingerprint != before.discovered_as.fingerprint
        assert (
            after.discovered_as.fingerprint == first.orchestrator._lifecycle_manager.get_mcp_server("moved").fingerprint
        )
        assert after.command == ["python", "-m", "moved-v2"]

    async def test_a_server_re_pointed_over_the_api_is_recorded_without_its_origin(self, durable) -> None:
        first = durable.boot()
        first.commands.send(
            CreateMcpServerCommand(
                mcp_server_id="remote",
                mode="remote",
                endpoint="https://93.184.216.34/mcp",
                source="discovery:docker",
                provenance=Provenance.DISCOVERY,
                discovered_as=DiscoveryOrigin(source_type="docker", fingerprint="abc"),
            )
        )

        first.commands.send(UpdateMcpServerCommand(mcp_server_id="remote", endpoint="https://93.184.216.35/mcp"))

        row = durable.row("remote")
        assert row is not None
        assert row.provenance is Provenance.HUMAN
        assert row.discovered_as is None
        assert durable.boot().is_static("remote")


class TestTheRowRecordsTheOrigin:
    def test_it_round_trips_through_the_stored_form(self) -> None:
        origin = DiscoveryOrigin(source_type="kubernetes", fingerprint="abc123", namespace="tools")
        row = McpServerConfigSnapshot(
            mcp_server_id="x", mode="subprocess", command=["py"], provenance=Provenance.DISCOVERY, discovered_as=origin
        )

        assert McpServerConfigSnapshot.from_dict(row.to_dict()).discovered_as == origin

    @pytest.mark.parametrize("stored", [None, "kubernetes", {"source_type": "kubernetes"}, {"fingerprint": "abc"}])
    def test_a_missing_or_malformed_one_reads_as_none(self, stored) -> None:
        data = {"mcp_server_id": "x", "mode": "subprocess", "command": ["py"], "discovered_as": stored}

        assert McpServerConfigSnapshot.from_dict(data).discovered_as is None

    def test_a_server_re_pointed_over_the_api_no_longer_records_one(self) -> None:
        # Re-pointing makes the endpoint a human's (HUMAN provenance); it is
        # then declared, and must not be handed back to discovery either.
        server = McpServer(
            mcp_server_id="x",
            mode="remote",
            endpoint="http://10.0.0.5:8080",
            provenance=Provenance.DISCOVERY,
            discovered_as=DiscoveryOrigin(source_type="docker", fingerprint="abc"),
        )

        server.reconfigure_connection("https://example.com/mcp", None)

        assert server.provenance is Provenance.HUMAN
        assert server.discovered_as is None
