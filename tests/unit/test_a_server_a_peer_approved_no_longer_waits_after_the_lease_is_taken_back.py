"""A server a peer approved no longer waits for approval when this replica takes the lease back (#2116).

Quarantine and pending are per instance and in memory. A replica that held a
changed definition there, lost the lease and followed while a peer approved it
kept the entry: it stayed listed as waiting, and an approval after the lease
came back applied it again -- or, when the peer had applied something newer
meanwhile, applied the older definition over the peer's choice.

The harness is #2111's: the real CRUD handlers over a SQLite backend and the
real orchestrator built by `create_discovery_orchestrator`. A peer's approval
is the row it writes to the fleet, carrying the origin of what it applied.
"""

from __future__ import annotations

import pytest

from mcp_hangar.application.commands.crud_commands import CreateMcpServerCommand, DeleteMcpServerCommand
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects.provenance import DiscoveryOrigin, Provenance

from . import test_a_discovered_server_stays_discovered_after_a_restart as restart
from .test_a_discovered_server_stays_discovered_after_a_restart import TTL
from .test_a_regained_lease_waits_a_full_ttl_before_removing import _command_of, _health_check_fails_for

durable = restart.durable


def _definition(name: str, variant: str = "") -> DiscoveredMcpServer:
    """What the test source reports for *name* with *variant*."""
    return DiscoveredMcpServer.create(
        name=name,
        source_type="filesystem",
        mode="subprocess",
        connection_info={"command": ["python", "-m", name + variant]},
        metadata={},
        ttl_seconds=TTL,
    )


def _a_peer_applies(gateway, name: str, variant: str) -> None:
    """The row a peer holding the lease writes when it registers or applies *variant* of *name*."""
    definition = _definition(name, variant)
    gateway.repo.add(
        name,
        McpServer(
            mcp_server_id=name,
            mode="subprocess",
            command=definition.connection_info["command"],
            provenance=Provenance.DISCOVERY,
            discovered_as=DiscoveryOrigin(source_type="filesystem", fingerprint=definition.fingerprint),
        ),
    )


async def _a_change_waits_then_the_lease_is_lost(durable):
    """Server "s" runs; its "-v2" fails validation and waits in quarantine; then this replica follows."""
    gateway = durable.boot()
    gateway.source.names = {"s"}
    await gateway.tick(0)
    _health_check_fails_for(gateway, "-v2")
    gateway.source.variant = "-v2"
    await gateway.tick()
    assert gateway.orchestrator._lifecycle_manager.is_quarantined("s")

    gateway.holds_the_lease = False
    await gateway.tick()
    return gateway


def _waiting(gateway) -> set[str]:
    orchestrator = gateway.orchestrator
    return set(orchestrator.get_quarantined()) | {p.name for p in orchestrator.get_pending_mcp_servers()}


@pytest.mark.asyncio
class TestAChangeAPeerApprovedWhileThisReplicaFollowed:
    async def test_it_no_longer_waits_and_the_server_is_governed(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        _a_peer_applies(gateway, "s", "-v2")
        created = len(gateway.commands.of(CreateMcpServerCommand))

        gateway.holds_the_lease = True
        await gateway.tick()

        assert _waiting(gateway) == set()
        assert gateway.orchestrator.is_tracking("s")
        # The source reports what runs: nothing to apply, nothing to quarantine.
        assert len(gateway.commands.of(CreateMcpServerCommand)) == created
        assert "-v2" in _command_of(gateway, "s")

        gateway.source.names = set()
        await gateway.run_past_the_ttl()
        assert "s" not in gateway.repo.get_all_ids()

    async def test_approving_it_finds_nothing_to_approve(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        digest = gateway.orchestrator.get_quarantined()["s"]["definition_digest"]
        _a_peer_applies(gateway, "s", "-v2")

        gateway.holds_the_lease = True
        await gateway.tick()
        sent = len(gateway.commands.sent)

        assert (await gateway.orchestrator.approve_mcp_server("s"))["approved"] is False
        assert (await gateway.orchestrator.approve_mcp_server("s", definition_digest=digest))["approved"] is False
        assert len(gateway.commands.sent) == sent

    async def test_a_new_server_a_peer_approved_is_governed(self, durable) -> None:
        gateway = durable.boot()
        _health_check_fails_for(gateway, "fresh")
        gateway.source.names = {"fresh"}
        await gateway.tick(0)
        assert gateway.orchestrator._lifecycle_manager.is_quarantined("fresh")
        gateway.holds_the_lease = False
        await gateway.tick()
        _a_peer_applies(gateway, "fresh", "")

        gateway.holds_the_lease = True
        await gateway.tick()

        assert _waiting(gateway) == set()
        assert gateway.orchestrator.is_tracking("fresh")


@pytest.mark.asyncio
class TestAChangeAPeerSupersededWhileThisReplicaFollowed:
    async def test_approving_the_older_one_does_not_overwrite_the_peers(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        digest = gateway.orchestrator.get_quarantined()["s"]["definition_digest"]
        _a_peer_applies(gateway, "s", "-v3")

        gateway.holds_the_lease = True
        await gateway.expiry_check()

        assert _waiting(gateway) == set()
        assert (await gateway.orchestrator.approve_mcp_server("s", definition_digest=digest))["approved"] is False
        assert (await gateway.orchestrator.approve_mcp_server("s"))["approved"] is False
        assert "-v3" in _command_of(gateway, "s")

    async def test_one_its_source_still_reports_waits_again_against_what_runs(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        _a_peer_applies(gateway, "s", "-v3")

        gateway.holds_the_lease = True
        await gateway.tick()

        # Dropped at the new tenure, quarantined again by the cycle after it.
        assert set(gateway.orchestrator.get_quarantined()) == {"s"}
        assert "-v3" in _command_of(gateway, "s")

    async def test_one_whose_server_a_peer_removed_no_longer_waits(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        digest = gateway.orchestrator.get_quarantined()["s"]["definition_digest"]
        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="s", source="discovery:filesystem"))

        gateway.holds_the_lease = True
        await gateway.expiry_check()

        assert _waiting(gateway) == set()
        assert (await gateway.orchestrator.approve_mcp_server("s", definition_digest=digest))["approved"] is False
        assert "s" not in gateway.repo.get_all_ids()


@pytest.mark.asyncio
class TestAChangeThatStillNeedsApproval:
    async def test_it_survives_the_regain_and_can_be_approved(self, durable) -> None:
        gateway = await _a_change_waits_then_the_lease_is_lost(durable)
        digest = gateway.orchestrator.get_quarantined()["s"]["definition_digest"]

        gateway.holds_the_lease = True
        await gateway.expiry_check()

        assert set(gateway.orchestrator.get_quarantined()) == {"s"}
        result = await gateway.orchestrator.approve_mcp_server("s", definition_digest=digest)
        assert result["status"] == "updated"
        assert "-v2" in _command_of(gateway, "s")


@pytest.mark.asyncio
class TestAPendingChange:
    async def _pending_then_followed(self, durable):
        gateway = durable.boot()
        gateway.source.names = {"s"}
        await gateway.tick(0)
        gateway.orchestrator._discovery_service._auto_register = False
        gateway.source.variant = "-v2"
        await gateway.tick()
        assert {p.name for p in gateway.orchestrator.get_pending_mcp_servers()} == {"s"}
        gateway.holds_the_lease = False
        await gateway.tick()
        return gateway

    async def test_one_a_peer_approved_no_longer_waits(self, durable) -> None:
        gateway = await self._pending_then_followed(durable)
        _a_peer_applies(gateway, "s", "-v2")

        gateway.holds_the_lease = True
        await gateway.expiry_check()

        assert _waiting(gateway) == set()

    async def test_one_still_needing_approval_survives(self, durable) -> None:
        gateway = await self._pending_then_followed(durable)

        gateway.holds_the_lease = True
        await gateway.expiry_check()

        assert {p.name for p in gateway.orchestrator.get_pending_mcp_servers()} == {"s"}


async def test_a_standalone_gateway_keeps_what_waits(durable) -> None:
    # Regression guard: a standalone gateway never begins a new tenure, so a
    # fleet row that would match the waiting entry drops nothing.
    orchestrator = DiscoveryOrchestrator()
    waiting = _definition("s", "-v2")
    orchestrator.restored_servers = lambda: [
        DiscoveredMcpServer.restored(
            name="s", source_type="filesystem", mode="subprocess", fingerprint=waiting.fingerprint, ttl_seconds=TTL
        )
    ]
    orchestrator._lifecycle_manager.quarantine(waiting, "starting")

    for _ in range(3):
        assert orchestrator._manages()

    assert set(orchestrator.get_quarantined()) == {"s"}
