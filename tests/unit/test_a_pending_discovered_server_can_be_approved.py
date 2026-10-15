"""`hangar_approve` and the REST approve register a pending discovered server (#1667).

With `auto_register: false` a discovered server that passed validation waits in
the discovery service's pending set, which `hangar_discovered` lists. Approval
only looked in quarantine, so it answered "not found in quarantine" and nothing
could ever register the server.
"""

from __future__ import annotations

from typing import Any

import pytest

from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer


class _Bus:
    def publish(self, event: Any) -> None:
        pass


def _server(name: str = "pending-one") -> DiscoveredMcpServer:
    return DiscoveredMcpServer.create(
        name=name,
        source_type="docker",
        mode="http",
        connection_info={"host": "10.88.0.7", "port": 8080},
        metadata={"runtime_addresses": ["10.88.0.7"]},
    )


@pytest.fixture
def orchestrator():
    config = DiscoveryConfig(auto_register=False)
    config.security.require_health_check = False
    orch = DiscoveryOrchestrator(config=config, event_bus=_Bus())
    orch.registered = []

    async def _accept(server: Any) -> bool:
        orch.registered.append(server.name)
        return True

    orch.on_register = _accept
    return orch


def _make_pending(orch: DiscoveryOrchestrator, server: DiscoveredMcpServer) -> None:
    orch._discovery_service._pending[server.name] = server


@pytest.mark.asyncio
async def test_approving_a_pending_server_registers_it(orchestrator):
    _make_pending(orchestrator, _server())
    assert [s.name for s in orchestrator.get_pending_mcp_servers()] == ["pending-one"]

    result = await orchestrator.approve_mcp_server("pending-one")

    assert result == {"approved": True, "mcp_server": "pending-one", "status": "registered"}
    assert orchestrator.registered == ["pending-one"]
    assert orchestrator.get_pending_mcp_servers() == []
    assert orchestrator._lifecycle_manager.get_mcp_server("pending-one") is not None


@pytest.mark.asyncio
async def test_an_approved_server_is_seen_as_unchanged_by_the_next_cycle(orchestrator):
    server = _server()
    _make_pending(orchestrator, server)
    await orchestrator.approve_mcp_server("pending-one")

    assert await orchestrator._process_mcp_server(server) == "skipped"
    assert orchestrator.registered == ["pending-one"]


@pytest.mark.asyncio
async def test_rejecting_a_pending_server_drops_it_without_registering(orchestrator):
    _make_pending(orchestrator, _server())

    result = await orchestrator.reject_mcp_server("pending-one")

    assert result == {"rejected": True, "mcp_server": "pending-one"}
    assert orchestrator.get_pending_mcp_servers() == []
    assert orchestrator.registered == []


@pytest.mark.asyncio
async def test_an_unknown_name_is_still_not_found(orchestrator):
    approved = await orchestrator.approve_mcp_server("nobody")
    rejected = await orchestrator.reject_mcp_server("nobody")

    assert approved["approved"] is False and "pending" in approved["error"]
    assert rejected["rejected"] is False


@pytest.mark.asyncio
async def test_a_quarantined_server_is_still_approved(orchestrator):
    server = _server("quarantined-one")
    orchestrator._lifecycle_manager.quarantine(server, "failed validation")

    result = await orchestrator.approve_mcp_server("quarantined-one")

    assert result["approved"] is True
    assert orchestrator.registered == ["quarantined-one"]
