"""A changed discovered definition replaces the running server on the next cycle (#2091).

An edited definition file or changed labels gave the server a new fingerprint,
and the orchestrator sent it to `on_register` again. That is a create, refused
with "McpServer already exists" on every cycle; and since the refused change
never refreshed the running definition's TTL, the lifecycle manager removed a
server its source still reported, and the next cycle created it again.

The change now goes through the create handler as a replacement: every check a
new registration passes runs first, and only a server discovery registered is
ever replaced. A change that is refused leaves the running definition running
and tracked, and is logged once. A source that needs approval holds the change
in pending, with the running definition untouched, until it is approved.

These drive the real service, orchestrator and lifecycle manager, the real
bootstrap callbacks and the real create/delete handlers over an in-memory
repository, with a clock the test moves.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
import structlog

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.commands.crud_handlers import CreateMcpServerHandler, DeleteMcpServerHandler
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.contracts.fleet import NotTheManagerError
from mcp_hangar.domain.contracts.persistence import ConcurrentModificationError
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource, SourcePolicyViolation
from mcp_hangar.domain.events import (
    McpServerDeregistered,
    McpServerDiscoveryConfigChanged,
    McpServerDiscoveryLost,
    McpServerRegistered,
)
from mcp_hangar.domain.exceptions import ValidationError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import L7Policy
from mcp_hangar.domain.repository import InMemoryMcpServerRepository
from mcp_hangar.domain.value_objects.provenance import Provenance
from mcp_hangar.infrastructure.discovery.filesystem_source import FilesystemDiscoverySource
from mcp_hangar.infrastructure.discovery.registry import create_source
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

    def of(self, kind: type) -> list[Any]:
        return [e for e in self.published if isinstance(e, kind)]


class _CommandBus:
    """Runs the real create and delete handlers, and remembers what it ran."""

    def __init__(self, repository, event_bus) -> None:
        self.sent: list[Any] = []
        self.fail_next: Exception | None = None
        self.create = CreateMcpServerHandler(repository=repository, event_bus=event_bus)
        self._delete = DeleteMcpServerHandler(repository=repository, event_bus=event_bus)

    def send(self, command: Any) -> Any:
        self.sent.append(command)
        if self.fail_next is not None:
            error, self.fail_next = self.fail_next, None
            raise error
        if isinstance(command, CreateMcpServerCommand):
            return self.create.handle(command)
        return self._delete.handle(command)

    def of(self, kind: type) -> list[Any]:
        return [c for c in self.sent if isinstance(c, kind)]


class _Source(DiscoverySource):
    """Reports `defs`: name -> (mode, connection_info)."""

    def __init__(self, mode: DiscoveryMode) -> None:
        super().__init__(mode=mode)
        self.defs: dict[str, tuple[str, dict[str, Any]]] = {}
        self.refuse: set[str] = set()

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

    def policy_violation(self, mcp_server: DiscoveredMcpServer) -> SourcePolicyViolation | None:
        if mcp_server.connection_info.get("command", [None])[-1] in self.refuse:
            return SourcePolicyViolation(reason="this source refuses it")
        return None


def _subprocess(module: str) -> tuple[str, dict[str, Any]]:
    return "subprocess", {"command": ["python", "-m", module]}


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

    def build(source: DiscoverySource, *, static: set[str] | None = None, **config_kwargs: Any):
        config = DiscoveryConfig(default_ttl_s=TTL, **config_kwargs)
        config.security.require_health_check = False
        orchestrator = DiscoveryOrchestrator(config=config, static_mcp_servers=static, event_bus=events)
        # As create_discovery_orchestrator wires them.
        orchestrator.on_register = bootstrap_discovery._on_mcp_server_register
        orchestrator.on_update = bootstrap_discovery._on_mcp_server_update
        orchestrator.on_deregister = bootstrap_discovery._on_mcp_server_deregister
        # Wired by start(), which would also start the background loops.
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        orchestrator.add_source(source)
        return orchestrator

    return SimpleNamespace(repo=repo, events=events, commands=commands, build=build)


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int = 5) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


def _command_of(repo, name: str) -> list[str] | None:
    server = repo.get(name)
    return None if server is None else list(server._command)


def _not_applied(captured: list[dict], level: str = "warning") -> list[dict]:
    return [e for e in captured if e["event"] == "discovery_change_not_applied" and e["log_level"] == level]


@pytest.mark.asyncio
class TestAChangeIsApplied:
    @pytest.mark.parametrize("mode", [DiscoveryMode.ADDITIVE, DiscoveryMode.AUTHORITATIVE])
    async def test_the_server_runs_the_new_command_on_the_next_cycle(self, fleet, mode) -> None:
        source = _Source(mode)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("new")
        with structlog.testing.capture_logs() as captured:
            await _tick(orchestrator)

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "new"]
        assert not [e for e in captured if e["event"] == "discovery_registration_failed"]
        assert orchestrator._lifecycle_manager.get_mcp_server("svc").connection_info["command"][-1] == "new"

    async def test_peers_see_a_deregistration_then_a_registration(self, fleet) -> None:
        # A peer's projection ignores a registration of a name it holds, so
        # the replacement has to reach it as a removal and then an addition.
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)
        fleet.events.published.clear()

        source.defs["svc"] = _subprocess("new")
        await _tick(orchestrator)

        kinds = [type(e) for e in fleet.events.published if isinstance(e, (McpServerDeregistered, McpServerRegistered))]
        assert kinds == [McpServerDeregistered, McpServerRegistered]

    async def test_the_change_is_recorded_once(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        # Refused by the control plane, so it is seen again every cycle.
        source.defs["svc"] = ("http", {"endpoint": "http://169.254.169.254/mcp"})
        for _ in range(4):
            await _tick(orchestrator)

        assert len(fleet.events.of(McpServerDiscoveryConfigChanged)) == 1

    async def test_a_source_at_its_cap_can_still_change_a_server(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        orchestrator._validator.config.max_mcp_servers_per_source = 1
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("new")
        await _tick(orchestrator)

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "new"]

    async def test_a_record_write_refused_after_the_stop_is_retried_not_kept(self, fleet) -> None:
        # The running server is already gone when the new row is refused: that
        # is not "the old one keeps running", and the next cycle creates it.
        writer = _FleetWriter([])
        fleet.commands.create = CreateMcpServerHandler(
            repository=fleet.repo, event_bus=fleet.events, fleet_writer=writer
        )
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("new")
        writer.refuse_next_save = True
        with structlog.testing.capture_logs() as captured:
            await _tick(orchestrator)
            await _tick(orchestrator)

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "new"]
        assert all(e["retried"] for e in _not_applied(captured))

    async def test_a_transient_failure_is_retried_next_cycle(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("new")
        fleet.commands.fail_next = NotTheManagerError("lease moved")
        await _tick(orchestrator)
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "old"]

        await _tick(orchestrator)
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "new"]


@pytest.mark.asyncio
class TestNothingElseIsTouched:
    async def test_an_unchanged_definition_restarts_nothing(self, fleet) -> None:
        source = _Source(DiscoveryMode.AUTHORITATIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("same")
        await _tick(orchestrator)
        first = fleet.repo.get("svc")

        for _ in range(10):
            await _tick(orchestrator)

        assert fleet.repo.get("svc") is first
        assert len(fleet.commands.of(CreateMcpServerCommand)) == 1
        assert not fleet.events.of(McpServerDeregistered)

    async def test_a_static_server_is_never_replaced(self, fleet) -> None:
        fleet.repo.add("static-svc", McpServer(mcp_server_id="static-svc", mode="subprocess", command=["static"]))
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source, static={"static-svc"})

        source.defs["static-svc"] = _subprocess("a")
        await _tick(orchestrator)
        source.defs["static-svc"] = _subprocess("b")
        await _tick(orchestrator)

        assert _command_of(fleet.repo, "static-svc") == ["static"]
        assert fleet.commands.sent == []

    async def test_a_server_an_operator_took_over_is_never_replaced(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("discovered")
        await _tick(orchestrator)
        # Deleted and created again over the API: the name is the operator's now.
        fleet.commands.send(DeleteMcpServerCommand(mcp_server_id="svc"))
        fleet.commands.send(CreateMcpServerCommand(mcp_server_id="svc", mode="subprocess", command=["mine"]))

        source.defs["svc"] = _subprocess("changed")
        with structlog.testing.capture_logs() as captured:
            for _ in range(5):
                await _tick(orchestrator, 10)

        assert _command_of(fleet.repo, "svc") == ["mine"]
        assert len(_not_applied(captured)) == 1


@pytest.mark.asyncio
class TestARefusedChangeKeepsTheRunningDefinition:
    async def test_a_control_plane_refusal_is_logged_once_and_not_retried(self, fleet, monkeypatch) -> None:
        monkeypatch.setattr(
            "mcp_hangar.domain.security.ssrf.socket.getaddrinfo",
            lambda *a, **k: [(None, None, None, None, ("10.0.0.9", 0))],
        )
        source = _Source(DiscoveryMode.AUTHORITATIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)
        creates = len(fleet.commands.of(CreateMcpServerCommand))

        # The metadata address: refused by the SSRF check whoever asks.
        source.defs["svc"] = ("http", {"endpoint": "http://169.254.169.254/mcp"})
        with structlog.testing.capture_logs() as captured:
            for _ in range(6):
                await _tick(orchestrator, 10)  # three TTLs

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "old"]
        assert len(fleet.commands.of(CreateMcpServerCommand)) == creates + 1
        assert len(_not_applied(captured)) == 1
        assert not fleet.events.of(McpServerDeregistered)

    async def test_a_refusal_of_a_coordinated_local_mode_leaves_the_running_server(self, fleet) -> None:
        fleet.commands.create._coordinated = lambda: True
        server = McpServer(
            mcp_server_id="svc",
            mode="remote",
            endpoint="http://10.0.0.9:8080/mcp",
            provenance=Provenance.DISCOVERY,
            runtime_addresses=frozenset({"10.0.0.9"}),
        )
        fleet.repo.add("svc", server)

        with pytest.raises(ValidationError):
            fleet.commands.send(
                CreateMcpServerCommand(
                    mcp_server_id="svc",
                    mode="subprocess",
                    command=["python", "-m", "local"],
                    provenance=Provenance.DISCOVERY,
                    replace_discovered=True,
                )
            )

        assert fleet.repo.get("svc") is server

    async def test_a_validation_failure_quarantines_the_change_and_keeps_the_old_one(self, fleet) -> None:
        source = _Source(DiscoveryMode.AUTHORITATIVE)
        orchestrator = fleet.build(source)
        source.defs["svc"] = _subprocess("old")
        await _tick(orchestrator)

        source.defs["svc"] = _subprocess("refused")
        source.refuse.add("refused")
        with structlog.testing.capture_logs() as captured:
            for _ in range(6):
                await _tick(orchestrator, 10)

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "old"]
        assert "svc" in orchestrator.get_quarantined()
        assert len(_not_applied(captured)) == 1
        assert not fleet.events.of(McpServerDiscoveryLost)

        # Approving the quarantined change applies it.
        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is True
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "refused"]


@pytest.mark.asyncio
class TestASourceThatNeedsApproval:
    async def test_a_change_waits_in_pending_and_the_old_one_keeps_running(self, fleet) -> None:
        source = _Source(DiscoveryMode.AUTHORITATIVE)
        orchestrator = fleet.build(source, auto_register=False)
        source.defs["svc"] = _subprocess("approved")
        await _tick(orchestrator)
        await orchestrator.approve_mcp_server("svc")
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "approved"]

        # Edited after the approval: the edit is not what was approved.
        source.defs["svc"] = _subprocess("edited")
        for _ in range(6):
            await _tick(orchestrator, 10)  # three TTLs

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "approved"]
        assert [p.connection_info["command"][-1] for p in orchestrator.get_pending_mcp_servers()] == ["edited"]
        assert not fleet.events.of(McpServerDeregistered)

    async def test_approving_the_change_applies_it(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source, auto_register=False)
        source.defs["svc"] = _subprocess("approved")
        await _tick(orchestrator)
        await orchestrator.approve_mcp_server("svc")
        source.defs["svc"] = _subprocess("edited")
        await _tick(orchestrator)

        result = await orchestrator.approve_mcp_server("svc")
        for _ in range(3):
            await _tick(orchestrator)

        assert result == {"approved": True, "mcp_server": "svc", "status": "updated"}
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "edited"]
        assert orchestrator.get_pending_mcp_servers() == []

    async def test_a_refused_approval_puts_the_change_back_in_pending(self, fleet) -> None:
        source = _Source(DiscoveryMode.ADDITIVE)
        orchestrator = fleet.build(source, auto_register=False)
        source.defs["svc"] = _subprocess("approved")
        await _tick(orchestrator)
        await orchestrator.approve_mcp_server("svc")
        source.defs["svc"] = ("http", {"endpoint": "http://169.254.169.254/mcp"})
        await _tick(orchestrator)

        result = await orchestrator.approve_mcp_server("svc")
        await _tick(orchestrator)

        assert result["approved"] is False
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "approved"]
        # Not applied unapproved by the next cycle: back where it waited.
        assert [p.name for p in orchestrator.get_pending_mcp_servers()] == ["svc"]


@pytest.mark.asyncio
class TestRealSources:
    @pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
    async def test_an_edited_definition_file_is_applied(self, fleet, tmp_path: Path) -> None:
        tmp_path.chmod(0o755)
        definition = tmp_path / "svc.yaml"
        definition.write_text("name: svc\nmode: subprocess\nconnection:\n  command: [python, -m, old]\n")
        definition.chmod(0o644)
        source = FilesystemDiscoverySource(path=str(tmp_path), mode=DiscoveryMode.ADDITIVE, watch=False)
        orchestrator = fleet.build(source)
        await _tick(orchestrator)
        assert _command_of(fleet.repo, "svc") == ["python", "-m", "old"]

        definition.write_text("name: svc\nmode: subprocess\nconnection:\n  command: [python, -m, new]\n")
        await _tick(orchestrator)

        assert _command_of(fleet.repo, "svc") == ["python", "-m", "new"]


def _container(**labels: str) -> MagicMock:
    container = MagicMock()
    container.id = "abc123def456789"
    container.name = "labelled"
    container.status = "running"
    container.labels = {
        "mcp.hangar.enabled": "true",
        "mcp.hangar.name": "labelled",
        "mcp.hangar.ttl": str(TTL),
        **labels,
    }
    container.image = MagicMock(tags=["some/image:1"], id="sha256:abc123def456")
    container.attrs = {"NetworkSettings": {"Ports": {}, "Networks": {"bridge": {"IPAddress": "10.88.0.23"}}}}
    return container


def _pod(**annotations: str) -> Mock:
    pod = Mock()
    pod.metadata = Mock(
        uid="uid-1",
        annotations={
            "mcp-hangar.io/enabled": "true",
            "mcp-hangar.io/name": "annotated",
            "mcp-hangar.io/ttl": str(TTL),
            **annotations,
        },
        labels={},
    )
    pod.metadata.name = "annotated-pod"
    pod.status = Mock(pod_ip="10.0.0.9", phase="Running")
    pod.spec = Mock(node_name="node-1")
    return pod


def _scanning(source: Any, parse: Any, items: list[Any]) -> Any:
    """*source* with its client replaced by *items*, parsed by the source's own parser."""

    async def discover() -> list[DiscoveredMcpServer]:
        return [found for item in items if (found := parse(item)) is not None]

    async def healthy() -> bool:
        return True

    source.discover = discover
    source.health_check = healthy
    return source


@pytest.fixture
def client_libraries():
    """Both sources are built without their client libraries, as CI's core install is."""
    with (
        patch("mcp_hangar.infrastructure.discovery.docker_source.DOCKER_AVAILABLE", True),
        patch("mcp_hangar.infrastructure.discovery.docker_source.docker"),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.KUBERNETES_AVAILABLE", True),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.config"),
        patch("mcp_hangar.infrastructure.discovery.kubernetes_source.client"),
    ):
        yield


@pytest.mark.asyncio
@pytest.mark.usefixtures("client_libraries")
class TestLabelsAndAnnotations:
    async def test_a_changed_docker_label_is_applied(self, fleet) -> None:
        containers = [_container(**{"mcp.hangar.command": "serve --old"})]
        source = create_source("docker", {})
        orchestrator = fleet.build(_scanning(source, source._parse_container, containers))
        await _tick(orchestrator)
        assert _command_of(fleet.repo, "labelled") == ["serve", "--old"]

        containers[0] = _container(**{"mcp.hangar.command": "serve --new"})
        await _tick(orchestrator)

        assert _command_of(fleet.repo, "labelled") == ["serve", "--new"]

    async def test_a_disallowed_mount_label_leaves_the_running_container(self, fleet) -> None:
        containers = [_container(**{"mcp.hangar.command": "serve"})]
        source = create_source("docker", {"mode": "additive"})
        orchestrator = fleet.build(_scanning(source, source._parse_container, containers))
        await _tick(orchestrator)

        containers[0] = _container(**{"mcp.hangar.command": "serve", "mcp.hangar.volumes": "/etc:/etc"})
        with structlog.testing.capture_logs() as captured:
            for _ in range(6):
                await _tick(orchestrator, 10)

        assert fleet.repo.get("labelled")._volumes in (None, [])
        refused = [e for e in captured if e["event"] == "docker_discovery_label_volumes_refused"]
        assert [e["log_level"] for e in refused][:1] == ["warning"]
        assert [e["log_level"] for e in refused][1:] == ["debug"] * 5
        assert not fleet.events.of(McpServerDeregistered)

    async def test_a_changed_kubernetes_annotation_is_applied(self, fleet) -> None:
        pods = [_pod(**{"mcp-hangar.io/port": "8080"})]
        source = create_source("kubernetes", {"in_cluster": False})
        orchestrator = fleet.build(_scanning(source, lambda pod: source._parse_pod(pod, "team-a"), pods))
        await _tick(orchestrator)
        assert fleet.repo.get("annotated")._endpoint == "http://10.0.0.9:8080/mcp"

        pods[0] = _pod(**{"mcp-hangar.io/port": "9090"})
        await _tick(orchestrator)

        assert fleet.repo.get("annotated")._endpoint == "http://10.0.0.9:9090/mcp"

    @pytest.mark.parametrize("mode", ["additive", "authoritative"])
    async def test_a_refused_local_mode_annotation(self, fleet, mode) -> None:
        pods = [_pod()]
        source = create_source("kubernetes", {"in_cluster": False, "mode": mode})
        orchestrator = fleet.build(_scanning(source, lambda pod: source._parse_pod(pod, "team-a"), pods))
        await _tick(orchestrator)

        pods[0] = _pod(**{"mcp-hangar.io/mode": "subprocess", "mcp-hangar.io/command": "sh -c id"})
        with structlog.testing.capture_logs() as captured:
            for _ in range(6):
                await _tick(orchestrator, 10)  # three TTLs

        refused = [e for e in captured if e["event"] == "kubernetes_discovery_local_mode_refused"]
        assert [e["log_level"] for e in refused] == ["warning"] + ["debug"] * 5
        server = fleet.repo.get("annotated")
        if mode == "additive":
            # The refused definition is not reported, so the running one stays.
            assert server is not None and server.mode_str == "remote"
        else:
            # An authoritative source that stopped vouching for a server takes it
            # away after its TTL, as for a file the trust checks refuse (#2065, #2089).
            assert server is None


class _FleetWriter:
    """Records the order of record writes; refuses a fenced delete or a save when told to."""

    def __init__(self, calls: list[str], holds_lease: bool = True) -> None:
        self.calls = calls
        self.holds_lease = holds_lease
        self.refuse_next_save = False
        self.saved: dict[str, Any] = {}

    def delete(self, mcp_server_id: str, *, fenced: bool = False) -> None:
        if fenced and not self.holds_lease:
            raise NotTheManagerError("the lease is held elsewhere")
        self.calls.append(f"delete fenced={fenced}")

    def save_versioned(self, snapshot: Any, expected_version: int | None, *_: Any) -> None:
        if self.refuse_next_save:
            self.refuse_next_save = False
            raise ConcurrentModificationError(snapshot.mcp_server_id, 0, 1)
        self.calls.append(f"save {snapshot.command}")
        self.saved[snapshot.mcp_server_id] = snapshot


def _discovered_server(name: str = "svc") -> McpServer:
    return McpServer(mcp_server_id=name, mode="subprocess", command=["old"], provenance=Provenance.DISCOVERY)


def _replacement(name: str = "svc", provenance: Provenance = Provenance.DISCOVERY) -> CreateMcpServerCommand:
    return CreateMcpServerCommand(
        mcp_server_id=name, mode="subprocess", command=["new"], provenance=provenance, replace_discovered=True
    )


class TestTheReplacementItself:
    def _handler(self, repo, calls: list[str], **kwargs: Any) -> CreateMcpServerHandler:
        def drain(name: str, timeout_s: float) -> bool:
            calls.append(f"drain {name} {timeout_s}")
            return True

        return CreateMcpServerHandler(repository=repo, event_bus=_RecordingBus(), drain=drain, **kwargs)

    def test_calls_in_flight_are_drained_and_the_record_delete_is_fenced(self) -> None:
        repo, calls = InMemoryMcpServerRepository(), []
        repo.add("svc", _discovered_server())

        self._handler(repo, calls, fleet_writer=_FleetWriter(calls)).handle(_replacement())

        assert calls == ["drain svc 30.0", "delete fenced=True", "save ['new']"]
        assert list(repo.get("svc")._command) == ["new"]

    def test_the_l7_policy_is_carried_over(self) -> None:
        # As a reload carries it: a changed annotation must not lift enforcement.
        repo, calls = InMemoryMcpServerRepository(), []
        writer = _FleetWriter(calls)
        old = _discovered_server()
        policy = L7Policy.from_dict({"tools": {"deny": ["exec"]}, "defaultAction": "Allow"})
        old.set_l7_policy(policy)
        repo.add("svc", old)

        self._handler(repo, calls, fleet_writer=writer).handle(_replacement())

        assert repo.get("svc")._l7_policy == policy
        assert writer.saved["svc"].l7_policy == policy.to_wire()

    def test_an_instance_without_the_lease_changes_nothing(self) -> None:
        repo, calls = InMemoryMcpServerRepository(), []
        old = _discovered_server()
        repo.add("svc", old)

        with pytest.raises(NotTheManagerError):
            self._handler(repo, calls, fleet_writer=_FleetWriter(calls, holds_lease=False)).handle(_replacement())

        assert repo.get("svc") is old

    def test_a_refused_definition_is_not_even_drained(self) -> None:
        repo, calls = InMemoryMcpServerRepository(), []
        old = _discovered_server()
        repo.add("svc", old)
        bad = CreateMcpServerCommand(
            mcp_server_id="svc",
            mode="remote",
            endpoint="http://169.254.169.254/mcp",
            provenance=Provenance.DISCOVERY,
            replace_discovered=True,
        )

        with pytest.raises(ValueError):
            self._handler(repo, calls).handle(bad)

        assert calls == []
        assert repo.get("svc") is old

    @pytest.mark.parametrize(
        ("existing", "command"),
        [
            # An operator's server, whoever asks.
            (McpServer(mcp_server_id="svc", mode="subprocess", command=["mine"]), _replacement()),
            # A request that sets the flag is not discovery.
            (_discovered_server(), _replacement(provenance=Provenance.HUMAN)),
            # Without the flag a create stays a create.
            (_discovered_server(), CreateMcpServerCommand(mcp_server_id="svc", mode="subprocess", command=["x"])),
        ],
    )
    def test_only_discovery_replaces_only_what_discovery_registered(self, existing, command) -> None:
        repo, calls = InMemoryMcpServerRepository(), []
        repo.add("svc", existing)

        with pytest.raises(ValidationError, match="already exists"):
            self._handler(repo, calls).handle(command)

        assert repo.get("svc") is existing
        assert calls == []
