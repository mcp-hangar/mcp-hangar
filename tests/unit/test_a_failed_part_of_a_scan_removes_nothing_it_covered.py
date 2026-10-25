"""A part of an authoritative scan that failed removes nothing it would have reported (#2089).

Since #2051 an authoritative source removes a server it stopped reporting once
its TTL runs out, and a scan that fails as a whole removes nothing. A scan that
failed in part still counted as absence: a Kubernetes namespace whose pods
could not be listed, or a definition file that would not parse, removed every
server registered from it -- a transient API error or a broken edit took
running servers away.

These drive the real Kubernetes and filesystem sources through the real
service, orchestrator and lifecycle manager, with a clock the test moves.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
import structlog

from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery import discovered_mcp_server as discovered_module
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.infrastructure.discovery import kubernetes_source
from mcp_hangar.infrastructure.discovery.filesystem_source import FilesystemDiscoverySource
from mcp_hangar.infrastructure.discovery.kubernetes_source import KubernetesDiscoverySource

TTL = 20


class _Clock:
    now = datetime(2026, 1, 1, tzinfo=UTC)


class _ClockedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[override]
        return _Clock.now


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    monkeypatch.setattr(discovered_module, "datetime", _ClockedDatetime)
    _Clock.now = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture(params=["package", "no package"])
def _kubernetes_api(request):
    """The source is built without a cluster, and both with and without the `kubernetes` package.

    CI's core install has no package, and there `ApiException` falls back to
    `Exception`, so the error arm catches everything a listing raises.
    """
    with (
        patch.object(kubernetes_source, "KUBERNETES_AVAILABLE", True),
        patch.object(kubernetes_source, "config"),
        patch.object(kubernetes_source, "client"),
    ):
        if request.param == "no package":
            with patch.object(kubernetes_source, "ApiException", Exception):
                yield
        else:
            yield


def _orchestrator(source: DiscoverySource) -> tuple[DiscoveryOrchestrator, list[str]]:
    """An orchestrator over *source* that records what it deregistered."""
    config = DiscoveryConfig(default_ttl_s=TTL)
    config.security.require_health_check = False
    orchestrator = DiscoveryOrchestrator(config=config)
    removed: list[str] = []

    async def _on_deregister(name: str, reason: str) -> None:
        removed.append(name)

    orchestrator.on_deregister = _on_deregister
    # Wired by start(), which would also start the background loops.
    orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
    orchestrator.add_source(source)
    return orchestrator, removed


async def _tick(orchestrator: DiscoveryOrchestrator, seconds: int) -> None:
    """One discovery cycle and one expiry check, `seconds` after the last."""
    _Clock.now += timedelta(seconds=seconds)
    await orchestrator.run_discovery_cycle()
    await orchestrator._lifecycle_manager._check_expirations()


def _registered(orchestrator: DiscoveryOrchestrator) -> set[str]:
    return {s.name for s in orchestrator._lifecycle_manager.get_all_mcp_servers().values()}


# -- Kubernetes ---------------------------------------------------------------


def _api_error(reason: str = "Forbidden") -> Exception:
    """An ApiException, whether or not the `kubernetes` package is installed."""
    if kubernetes_source.ApiException is Exception:  # the fallback without the package
        exc = Exception(reason)
        exc.reason = reason  # type: ignore[attr-defined]
        return exc
    return kubernetes_source.ApiException(status=403, reason=reason)


def _pod(name: str, ip: str):
    pod = Mock()
    pod.metadata = Mock(
        uid=f"uid-{name}",
        annotations={"mcp-hangar.io/enabled": "true", "mcp-hangar.io/name": name, "mcp-hangar.io/ttl": str(TTL)},
        labels={},
    )
    pod.metadata.name = name
    pod.status = Mock(pod_ip=ip, phase="Running")
    pod.spec = Mock(node_name="node-1")
    return pod


class _Cluster:
    """Pods per namespace, and what listing a namespace raises instead."""

    def __init__(self, pods: dict[str, list]) -> None:
        self.pods = pods
        self.failing: dict[str, Exception] = {}
        self.namespaces_fail = False

    def list_namespaced_pod(self, namespace: str, label_selector: str | None = None) -> Any:
        if namespace in self.failing:
            raise self.failing[namespace]
        return MagicMock(items=self.pods.get(namespace, []))

    def list_namespace(self) -> Any:
        if self.namespaces_fail:
            raise _api_error("Service Unavailable")
        return MagicMock(items=[SimpleNamespace(metadata=SimpleNamespace(name=n)) for n in self.pods])


def _kubernetes(cluster: _Cluster, namespaces: list[str] | None) -> KubernetesDiscoverySource:
    source = KubernetesDiscoverySource(namespaces=namespaces, in_cluster=False, denied_namespaces=set())
    v1 = MagicMock()
    v1.list_namespaced_pod.side_effect = cluster.list_namespaced_pod
    v1.list_namespace.side_effect = cluster.list_namespace
    source._v1 = v1
    source._initialized = True
    return source


@pytest.mark.asyncio
@pytest.mark.usefixtures("_kubernetes_api")
class TestOneNamespaceFails:
    @pytest.mark.parametrize("error", [lambda: _api_error("Forbidden"), lambda: TimeoutError("read timed out")])
    async def test_its_servers_stay_and_the_other_namespace_is_reconciled(self, error) -> None:
        cluster = _Cluster({"team-a": [_pod("a1", "10.0.0.1")], "team-b": [_pod("b1", "10.0.1.1")]})
        orchestrator, removed = _orchestrator(_kubernetes(cluster, ["team-a", "team-b"]))
        await _tick(orchestrator, 0)
        assert _registered(orchestrator) == {"a1", "b1"}

        cluster.failing["team-a"] = error()
        cluster.pods["team-b"] = []
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"a1"}
        assert removed == ["b1"]

    async def test_a_server_gone_while_its_namespace_failed_goes_once_it_answers(self) -> None:
        cluster = _Cluster({"team-a": [_pod("a1", "10.0.0.1")]})
        orchestrator, removed = _orchestrator(_kubernetes(cluster, ["team-a"]))
        await _tick(orchestrator, 0)

        cluster.failing["team-a"] = _api_error()
        for _ in range(10):
            await _tick(orchestrator, 5)
        assert _registered(orchestrator) == {"a1"}

        del cluster.failing["team-a"]
        cluster.pods["team-a"] = []
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert removed == ["a1"]

    async def test_a_server_still_there_when_its_namespace_answers_again_stays(self) -> None:
        cluster = _Cluster({"team-a": [_pod("a1", "10.0.0.1")]})
        orchestrator, removed = _orchestrator(_kubernetes(cluster, ["team-a"]))
        await _tick(orchestrator, 0)

        cluster.failing["team-a"] = _api_error()
        for _ in range(10):
            await _tick(orchestrator, 5)
        del cluster.failing["team-a"]
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"a1"}
        assert removed == []

    async def test_a_namespace_that_keeps_failing_is_warned_about_every_cycle(self) -> None:
        cluster = _Cluster({"team-a": [_pod("a1", "10.0.0.1")]})
        orchestrator, _ = _orchestrator(_kubernetes(cluster, ["team-a"]))
        await _tick(orchestrator, 0)

        cluster.failing["team-a"] = _api_error()
        with structlog.testing.capture_logs() as captured:
            for _ in range(6):
                await _tick(orchestrator, 5)

        kept = [e for e in captured if e["event"] == "discovery_part_failed_servers_kept"]
        assert len(kept) == 6
        assert all(e["log_level"] == "warning" for e in kept)
        assert kept[0]["part"] == "namespace team-a"
        assert kept[0]["kept"] == ["a1"]
        assert kept[0]["past_ttl"] == []
        # A part broken for longer than the TTL says so.
        assert kept[-1]["past_ttl"] == ["a1"]

    async def test_listing_all_namespaces_failing_removes_nothing(self) -> None:
        # With no namespaces configured the source lists them first. That
        # listing failing used to answer "no namespaces", which read as every
        # pod gone while the API kept answering health checks.
        cluster = _Cluster({"team-a": [_pod("a1", "10.0.0.1")]})
        orchestrator, removed = _orchestrator(_kubernetes(cluster, None))
        await _tick(orchestrator, 0)
        assert _registered(orchestrator) == {"a1"}

        cluster.namespaces_fail = True
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"a1"}
        assert removed == []

    async def test_one_pod_that_does_not_parse_does_not_hide_its_neighbours(self) -> None:
        bad = _pod("bad", "10.0.0.9")
        bad.metadata.annotations["mcp-hangar.io/port"] = "not-a-number"
        cluster = _Cluster({"team-a": [bad, _pod("a1", "10.0.0.1")]})

        found = await _kubernetes(cluster, ["team-a"]).discover()

        assert [s.name for s in found] == ["a1"]


# -- Filesystem ---------------------------------------------------------------


def _definition(name: str) -> str:
    return f"name: {name}\nmode: subprocess\nttl: {TTL}\nconnection:\n  command: [python, -m, {name}]\n"


def _filesystem(directory: Path) -> FilesystemDiscoverySource:
    return FilesystemDiscoverySource(path=str(directory), mode=DiscoveryMode.AUTHORITATIVE, watch=False)


@pytest.fixture
def directory(tmp_path: Path) -> Path:
    tmp_path.chmod(0o755)
    (tmp_path / "steady.yaml").write_text(_definition("steady"))
    (tmp_path / "edited.yaml").write_text(_definition("edited"))
    return tmp_path


@pytest.mark.asyncio
class TestOneFileFails:
    async def test_a_file_that_stops_parsing_keeps_its_server(self, directory: Path) -> None:
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)
        assert _registered(orchestrator) == {"steady", "edited"}

        (directory / "edited.yaml").write_text("name: edited\nconnection: [unclosed\n")
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"steady", "edited"}
        assert removed == []

    async def test_a_file_fixed_after_its_ttl_keeps_its_server(self, directory: Path) -> None:
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)
        (directory / "edited.yaml").write_text("name: edited\nconnection: [unclosed\n")
        for _ in range(10):
            await _tick(orchestrator, 5)

        (directory / "edited.yaml").write_text(_definition("edited"))
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"steady", "edited"}
        assert removed == []

    async def test_a_file_that_fails_to_load_keeps_its_server(self, directory: Path) -> None:
        # Valid YAML whose content does not load: a port that is not a number.
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)

        (directory / "edited.yaml").write_text("name: edited\nmode: http\nconnection:\n  port: nope\n")
        for _ in range(10):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"steady", "edited"}
        assert removed == []

    async def test_a_broken_file_that_is_then_deleted_removes_its_server(self, directory: Path) -> None:
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)
        (directory / "edited.yaml").write_text("name: edited\nconnection: [unclosed\n")
        for _ in range(10):
            await _tick(orchestrator, 5)

        (directory / "edited.yaml").unlink()
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert _registered(orchestrator) == {"steady"}
        assert removed == ["edited"]

    async def test_a_deleted_file_still_removes_its_server(self, directory: Path) -> None:
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)

        (directory / "edited.yaml").unlink()
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert removed == ["edited"]

    async def test_a_file_refused_by_the_trust_checks_still_removes_its_server(self, directory: Path) -> None:
        # Intentional since 2.34.2: a world-writable definition is refused, and
        # refusing it is meant to take the server away.
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)

        (directory / "edited.yaml").chmod(0o666)
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert removed == ["edited"]

    async def test_a_disabled_definition_still_removes_its_server(self, directory: Path) -> None:
        orchestrator, removed = _orchestrator(_filesystem(directory))
        await _tick(orchestrator, 0)

        (directory / "edited.yaml").write_text("enabled: false\n" + _definition("edited"))
        for _ in range(6):
            await _tick(orchestrator, 5)

        assert removed == ["edited"]


# -- Others -------------------------------------------------------------------


class _PartlyFailing(DiscoverySource):
    """Says every part failed, so only the mode decides what happens."""

    def __init__(self, mode: DiscoveryMode) -> None:
        super().__init__(mode=mode)
        self.names = {"one"}

    @property
    def source_type(self) -> str:
        return "custom"

    async def discover(self) -> list[DiscoveredMcpServer]:
        return [
            DiscoveredMcpServer.create(
                name=n,
                source_type="custom",
                mode="subprocess",
                connection_info={"command": ["python", "-m", n]},
                metadata={},
                ttl_seconds=TTL,
            )
            for n in sorted(self.names)
        ]

    async def health_check(self) -> bool:
        return True

    def unscanned_part(self, mcp_server: DiscoveredMcpServer) -> str | None:
        return "everything"


class TestOthersAreUntouched:
    @pytest.mark.asyncio
    async def test_an_additive_source_never_removes_and_never_warns(self) -> None:
        source = _PartlyFailing(DiscoveryMode.ADDITIVE)
        orchestrator, removed = _orchestrator(source)
        await _tick(orchestrator, 0)

        source.names = set()
        with structlog.testing.capture_logs() as captured:
            for _ in range(10):
                await _tick(orchestrator, 5)

        assert removed == []
        assert not [e for e in captured if e["event"] == "discovery_part_failed_servers_kept"]

    def test_a_source_that_does_not_say_has_no_failed_parts(self) -> None:
        class _Plain(DiscoverySource):
            source_type = "plain"  # type: ignore[assignment]

            async def discover(self) -> list[DiscoveredMcpServer]:
                return []

            async def health_check(self) -> bool:
                return True

        server = DiscoveredMcpServer.create(
            name="x", source_type="plain", mode="subprocess", connection_info={}, metadata={}
        )
        assert _Plain().unscanned_part(server) is None
