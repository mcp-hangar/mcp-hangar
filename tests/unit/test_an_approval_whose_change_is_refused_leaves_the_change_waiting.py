"""An approval of a change whose replacement is refused leaves the change waiting (#2138).

`_approve_change` took the changed definition out of quarantine or pending and,
when `on_update` refused or raised, put back only the running definition. The
change was gone from `hangar_quarantine`, `GET /quarantined` and the pending
listing until the next cycle parked it again, and the approval answered with a
bare error instead of the shape of the other approval refusals.

A refused change now answers `approved: false` with the reason, as a refused
registration does (#2136), and waits again where it waited, with the same
reason and time; tracking stays on the running definition, and the resolver and
the per-source counts are left as they were.

These drive the real service, orchestrator and lifecycle manager, the real
bootstrap callbacks and the real create handler over an in-memory repository.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.server.bootstrap import discovery as bootstrap_discovery

TTL = 20
RUNNING = ["python", "-m", "running"]


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
    """Runs the real create and delete handlers."""

    def __init__(self, repository, event_bus) -> None:
        self._create = CreateMcpServerHandler(repository=repository, event_bus=event_bus)
        self._delete = DeleteMcpServerHandler(repository=repository, event_bus=event_bus)

    def send(self, command: Any) -> Any:
        if isinstance(command, CreateMcpServerCommand):
            return self._create.handle(command)
        return self._delete.handle(command)


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


@pytest.fixture
def fleet(monkeypatch):
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)

    repo = InMemoryMcpServerRepository()
    events = _Bus()
    commands = _CommandBus(repo, events)
    monkeypatch.setattr(
        bootstrap_discovery,
        "get_runtime",
        lambda: SimpleNamespace(repository=repo, command_bus=commands, event_bus=events),
    )

    async def changed(waits_in: str, change: tuple[str, dict[str, Any]]) -> DiscoveryOrchestrator:
        """An orchestrator running "svc" as RUNNING, with *change* of it waiting in *waits_in*."""
        config = DiscoveryConfig(default_ttl_s=TTL, auto_register=waits_in != "pending")
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, event_bus=events)
        # As create_discovery_orchestrator wires them.
        orchestrator.on_register = bootstrap_discovery._on_mcp_server_register
        orchestrator.on_update = bootstrap_discovery._on_mcp_server_update
        orchestrator.on_deregister = bootstrap_discovery._on_mcp_server_deregister
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        source = _Source()
        orchestrator.add_source(source)

        source.defs["svc"] = ("subprocess", {"command": RUNNING})
        await _tick(orchestrator)
        if waits_in == "pending":
            await orchestrator.approve_mcp_server("svc")
        else:
            # A limit quarantines the change, and an approval overrides a limit.
            orchestrator._validator.config.max_registration_rate = 0
        assert _command_of(repo, "svc") == RUNNING

        source.defs["svc"] = change
        await _tick(orchestrator)
        return orchestrator

    return SimpleNamespace(repo=repo, changed=changed)


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int = 5) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


def _command_of(repo, name: str) -> list[str] | None:
    server = repo.get(name)
    return None if server is None else list(server._command)


def _waiting(orchestrator: DiscoveryOrchestrator, waits_in: str) -> dict[str, Any]:
    """What waits under "svc" as the listings show it, or {} when nothing does."""
    if waits_in == "quarantine":
        entry = orchestrator.get_quarantined().get("svc")
        return {} if entry is None else {k: entry[k] for k in ("definition_digest", "reason", "quarantine_time")}
    pending = [p for p in orchestrator.get_pending_mcp_servers() if p.name == "svc"]
    return {"definition_digest": pending[0].definition_digest} if pending else {}


def _state(orchestrator: DiscoveryOrchestrator) -> dict[str, Any]:
    """Tracking, the resolver and the per-source counts: what a refused approval must leave alone."""
    return {
        "tracked": orchestrator._lifecycle_manager.get_all_mcp_servers(),
        "registered": orchestrator._discovery_service.get_registered_mcp_servers(),
        "counts": dict(orchestrator._validator._mcp_server_counts),
    }


# The create handler refuses an endpoint in the metadata range, so the bootstrap
# callback raises DiscoveryChangeRefused while the running server stays.
REFUSED = ("http", {"endpoint": "http://169.254.169.254/mcp"})
CHANGED = ("subprocess", {"command": ["python", "-m", "changed"]})


@pytest.mark.asyncio
@pytest.mark.parametrize("waits_in", ["quarantine", "pending"])
class TestARefusedChange:
    async def test_a_refused_replacement_is_not_approved_and_the_change_still_waits(self, fleet, waits_in) -> None:
        orchestrator = await fleet.changed(waits_in, REFUSED)
        before = _waiting(orchestrator, waits_in)
        assert before
        state = _state(orchestrator)

        result = await orchestrator.approve_mcp_server("svc", definition_digest=before["definition_digest"])

        assert _waiting(orchestrator, waits_in) == before
        assert _state(orchestrator) == state
        assert result["approved"] is False
        assert result["mcp_server"] == "svc"
        assert result["error"].startswith("approval refused: ")
        assert "status" not in result
        assert _command_of(fleet.repo, "svc") == RUNNING

        # Not applied by the next cycle unapproved, and still there to approve.
        await _tick(orchestrator)
        assert _waiting(orchestrator, waits_in)["definition_digest"] == before["definition_digest"]
        assert _command_of(fleet.repo, "svc") == RUNNING

    async def test_a_raising_replacement_is_not_approved_and_the_change_still_waits(self, fleet, waits_in) -> None:
        orchestrator = await fleet.changed(waits_in, CHANGED)
        before = _waiting(orchestrator, waits_in)
        assert before
        state = _state(orchestrator)

        async def _raise(server: DiscoveredMcpServer) -> None:
            raise RuntimeError("the control plane is unreachable")

        update, orchestrator.on_update = orchestrator.on_update, _raise
        result = await orchestrator.approve_mcp_server("svc", definition_digest=before["definition_digest"])

        assert _waiting(orchestrator, waits_in) == before
        assert _state(orchestrator) == state
        assert result == {
            "approved": False,
            "mcp_server": "svc",
            "error": "approval refused: the control plane is unreachable",
        }
        assert _command_of(fleet.repo, "svc") == RUNNING

        # The cause has passed, and the next cycle still leaves the change waiting.
        orchestrator.on_update = update
        await _tick(orchestrator)
        assert _waiting(orchestrator, waits_in)["definition_digest"] == before["definition_digest"]
        assert _command_of(fleet.repo, "svc") == RUNNING

    async def test_an_accepted_replacement_is_approved_as_before(self, fleet, waits_in) -> None:
        orchestrator = await fleet.changed(waits_in, CHANGED)
        before = _waiting(orchestrator, waits_in)
        counts = dict(orchestrator._validator._mcp_server_counts)

        result = await orchestrator.approve_mcp_server("svc", definition_digest=before["definition_digest"])

        assert result == {"approved": True, "mcp_server": "svc", "status": "updated"}
        assert _waiting(orchestrator, waits_in) == {}
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "changed"]
        assert orchestrator._lifecycle_manager.get_mcp_server("svc").definition_digest == before["definition_digest"]
        assert orchestrator._validator._mcp_server_counts == counts

        await _tick(orchestrator)
        assert _waiting(orchestrator, waits_in) == {}
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "changed"]
