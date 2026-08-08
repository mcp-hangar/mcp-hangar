"""The four discovery source-management defects exposed in 2.5.0-rc.4.

Each of these routes became reachable once configured sources were given an
addressable id, and each handler then turned out broken:

A. POST /sources/{id}/scan never awaited trigger_discovery (the coroutine was
   dropped) and read a result key the cycle never produces -- so it answered 200
   with a fabricated count of 0 and ran no scan.
B. enable/disable/config-update mutated only the stored spec, never the running
   source -- so a "disabled" source kept being scanned and GET still read enabled.
C. the listing re-advertised an addressable id for a source deleted from the
   registry but still running, so scan/enable 404'd for an id the listing showed.
D. the MCP `hangar_sources` tool returned sources with no id at all.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

from mcp_hangar.application.commands.discovery_commands import TriggerSourceScanCommand
from mcp_hangar.application.commands.discovery_handlers import TriggerSourceScanHandler
from mcp_hangar.application.discovery import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.application.discovery.discovery_registry import DiscoveryRegistry
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.value_objects.discovery import DiscoverySourceSpec, config_source_id


class _FakeSource(DiscoverySource):
    """A source whose scan output and reconfiguration are observable."""

    def __init__(self, source_type: str, servers: list[DiscoveredMcpServer] | None = None) -> None:
        super().__init__(mode=DiscoveryMode.ADDITIVE)
        self._type = source_type
        self._servers = servers or []
        self.discover_calls = 0
        self.applied_configs: list[dict] = []

    @property
    def source_type(self) -> str:
        return self._type

    async def discover(self) -> list[DiscoveredMcpServer]:
        self.discover_calls += 1
        return list(self._servers)

    async def health_check(self) -> bool:
        return True

    def apply_config(self, config: dict) -> None:
        self.applied_configs.append(dict(config))


def _server(name: str, source_type: str = "docker") -> DiscoveredMcpServer:
    return DiscoveredMcpServer.create(
        name=name,
        source_type=source_type,
        mode="subprocess",
        connection_info={"command": ["echo", name]},
    )


def _orchestrator_with(source: DiscoverySource) -> DiscoveryOrchestrator:
    orchestrator = DiscoveryOrchestrator(config=DiscoveryConfig(enabled=True, auto_register=True))
    orchestrator.add_source(source)
    return orchestrator


def _registry_for(orchestrator: DiscoveryOrchestrator, source_type: str) -> tuple[DiscoveryRegistry, str]:
    """A registry holding the config-derived spec for one running source."""
    registry = DiscoveryRegistry(orchestrator=orchestrator)
    source_id = config_source_id(source_type)
    registry.register_source(
        DiscoverySourceSpec(source_id=source_id, source_type=source_type, mode=DiscoveryMode.ADDITIVE)
    )
    return registry, source_id


# ---------------------------------------------------------------------------
# Bug A -- the scan actually runs a cycle and returns the real discovered count
# ---------------------------------------------------------------------------


class TestScanActuallyRuns:
    def test_scan_awaits_the_cycle_and_reports_the_real_count(self) -> None:
        source = _FakeSource("docker", servers=[_server("a"), _server("b")])
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")
        handler = TriggerSourceScanHandler(registry=registry)

        result = handler.handle(TriggerSourceScanCommand(source_id=source_id))

        # The coroutine was actually awaited: the source was scanned...
        assert source.discover_calls == 1
        # ...and the response carries the real discovered count, not a fabricated 0.
        assert result["scan_triggered"] is True
        assert result["mcp_servers_found"] == 2

    def test_scan_reports_zero_only_when_nothing_is_discovered(self) -> None:
        source = _FakeSource("docker", servers=[])
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")
        handler = TriggerSourceScanHandler(registry=registry)

        result = handler.handle(TriggerSourceScanCommand(source_id=source_id))

        assert source.discover_calls == 1
        assert result["mcp_servers_found"] == 0

    def test_blocking_bridge_runs_the_cycle_when_no_loop_is_running(self) -> None:
        source = _FakeSource("docker", servers=[_server("a")])
        orchestrator = _orchestrator_with(source)

        out = orchestrator.trigger_discovery_blocking()

        assert out["discovered_count"] == 1
        assert source.discover_calls == 1


# ---------------------------------------------------------------------------
# Bug B -- a disabled source stops being scanned and GET agrees
# ---------------------------------------------------------------------------


class TestToggleReachesTheRunningSource:
    def test_disable_stops_the_source_being_scanned(self) -> None:
        source = _FakeSource("docker", servers=[_server("a")])
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        registry.disable_source(source_id)

        # The live instance the cycle reads is now disabled...
        assert orchestrator.get_source("docker").is_enabled is False
        # ...so a cycle does not scan it.
        asyncio.run(orchestrator.run_discovery_cycle())
        assert source.discover_calls == 0

    def test_get_agrees_with_the_toggle(self) -> None:
        source = _FakeSource("docker")
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        registry.disable_source(source_id)

        statuses = asyncio.run(orchestrator.get_sources_status())
        docker = next(s for s in statuses if s["source_type"] == "docker")
        # The listing no longer contradicts the 200 the disable returned.
        assert docker["is_enabled"] is False

    def test_enable_resumes_scanning(self) -> None:
        source = _FakeSource("docker", servers=[_server("a")])
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        registry.disable_source(source_id)
        registry.enable_source(source_id)

        assert orchestrator.get_source("docker").is_enabled is True
        asyncio.run(orchestrator.run_discovery_cycle())
        assert source.discover_calls == 1

    def test_config_update_reaches_the_running_source(self) -> None:
        source = _FakeSource("docker")
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        registry.update_source(source_id, config={"socket_path": "/x.sock"})

        assert source.applied_configs == [{"socket_path": "/x.sock"}]


# ---------------------------------------------------------------------------
# Bug C -- a deleted-but-still-running source is not advertised as scan-able
# ---------------------------------------------------------------------------


class _Ctx:
    def __init__(self, orchestrator: DiscoveryOrchestrator, registry: DiscoveryRegistry) -> None:
        self.discovery_orchestrator = orchestrator
        self.discovery_registry = registry


class _Req:
    path_params: dict = {}


class TestListingAndRoutesAgree:
    def test_a_registered_source_is_listed_with_its_addressable_id(self) -> None:
        source = _FakeSource("docker")
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        from mcp_hangar.server.api import discovery as api

        with patch.object(api, "get_context", return_value=_Ctx(orchestrator, registry)):
            body = asyncio.run(api.list_sources(_Req())).body

        import json

        sources = json.loads(body)["sources"]
        docker = next(s for s in sources if s["source_type"] == "docker")
        # The id the listing shows is exactly the one the routes accept.
        assert docker["id"] == source_id
        assert registry.get_source(docker["id"]) is not None

    def test_a_deleted_source_still_running_is_listed_without_an_id(self) -> None:
        source = _FakeSource("docker")
        orchestrator = _orchestrator_with(source)
        registry, source_id = _registry_for(orchestrator, "docker")

        # DELETE removed the spec; the orchestrator still runs the source.
        registry.unregister_source(source_id)

        from mcp_hangar.server.api import discovery as api

        with patch.object(api, "get_context", return_value=_Ctx(orchestrator, registry)):
            body = asyncio.run(api.list_sources(_Req())).body

        import json

        sources = json.loads(body)["sources"]
        docker = next(s for s in sources if s["source_type"] == "docker")
        # Still visible (it IS running) but no longer addressable: no id to 404 on.
        assert "id" not in docker


# ---------------------------------------------------------------------------
# Bug D -- the MCP discovery tool returns sources WITH ids
# ---------------------------------------------------------------------------


class _FakeMCP:
    def __init__(self) -> None:
        self.tools: dict = {}

    def tool(self, name: str | None = None, **_kw):
        def deco(fn):
            self.tools[name] = fn
            return fn

        return deco


class _ToolCtx:
    def __init__(self, orchestrator: DiscoveryOrchestrator) -> None:
        self.discovery_orchestrator = orchestrator


class TestMcpToolCarriesIds:
    def test_hangar_sources_returns_sources_with_ids(self) -> None:
        source = _FakeSource("docker")
        orchestrator = _orchestrator_with(source)

        from mcp_hangar.server.tools import discovery as tools

        mcp = _FakeMCP()
        tools.register_discovery_tools(mcp)
        hangar_sources = mcp.tools["hangar_sources"]

        with patch.object(tools, "get_context", return_value=_ToolCtx(orchestrator)):
            with patch.object(tools, "check_rate_limit", lambda *_a, **_k: None):
                out = asyncio.run(hangar_sources())

        docker = next(s for s in out["sources"] if s["source_type"] == "docker")
        assert docker["id"] == config_source_id("docker")
