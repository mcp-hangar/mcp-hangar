"""A replica that takes the management lease back waits a full TTL before removing anything (#2111).

It tracked servers while it held the lease, lost it, and followed while another
replica managed. The last sightings it kept ran out meanwhile, so the first
expiry check after it took the lease back deregistered servers its sources
still reported, before a discovery cycle could see them.

The same harness as #2110's restart tests: the real create and delete handlers
over a SQLite backend, `create_discovery_orchestrator` building the real
orchestrator and lifecycle manager over it, and a clock the test moves.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from mcp_hangar.application.commands.crud_commands import DeleteMcpServerCommand
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryOrchestrator
from mcp_hangar.application.discovery.security_validator import ValidationReport, ValidationResult
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode
from mcp_hangar.domain.exceptions import McpServerNotHereError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.value_objects.provenance import Provenance

from . import test_a_discovered_server_stays_discovered_after_a_restart as restart
from .test_a_discovered_server_stays_discovered_after_a_restart import TTL, _Clock, _Source

#: The restart tests' backend and clock.
durable = restart.durable


async def _held_then_followed(durable, *names: str):
    """A gateway that registered *names* with the lease, then followed for three TTLs."""
    gateway = durable.boot()
    gateway.source.names = set(names)
    await gateway.tick(0)
    assert set(names) <= set(gateway.repo.get_all_ids())

    gateway.holds_the_lease = False
    await gateway.tick()
    _Clock.now += timedelta(seconds=TTL * 3)
    await gateway.tick()
    return gateway


@pytest.mark.asyncio
class TestTakingTheLeaseBack:
    async def test_servers_still_reported_are_not_removed(self, durable) -> None:
        gateway = await _held_then_followed(durable, "one", "two")

        gateway.holds_the_lease = True
        # Before any cycle: the expiry loop can see the lease first.
        await gateway.expiry_check()
        assert set(gateway.repo.get_all_ids()) == {"one", "two"}

        await gateway.run_past_the_ttl()
        assert set(gateway.repo.get_all_ids()) == {"one", "two"}

    async def test_one_no_longer_reported_is_removed_a_full_ttl_after_taking_it(self, durable) -> None:
        gateway = await _held_then_followed(durable, "kept", "gone")
        gateway.source.names = {"kept"}

        gateway.holds_the_lease = True
        await gateway.expiry_check()
        regained = _Clock.now
        assert set(gateway.repo.get_all_ids()) == {"kept", "gone"}

        while _Clock.now + timedelta(seconds=5) <= regained + timedelta(seconds=TTL):
            await gateway.tick()
        assert set(gateway.repo.get_all_ids()) == {"kept", "gone"}

        await gateway.tick()
        await gateway.tick()
        assert set(gateway.repo.get_all_ids()) == {"kept"}

    async def test_a_server_a_peer_removed_meanwhile_is_registered_again_when_reported(self, durable) -> None:
        # Kept in tracking, it read as already registered and never came back.
        gateway = await _held_then_followed(durable, "back")
        gateway.commands.send(DeleteMcpServerCommand(mcp_server_id="back", source="discovery:filesystem"))
        assert "back" not in gateway.repo.get_all_ids()

        gateway.holds_the_lease = True
        await gateway.tick()

        assert "back" in gateway.repo.get_all_ids()

    async def test_a_server_re_pointed_over_the_api_meanwhile_is_no_longer_governed(self, durable) -> None:
        gateway = await _held_then_followed(durable, "mine")
        gateway.source.names = set()
        gateway.repo.add(
            "mine",
            McpServer(
                mcp_server_id="mine",
                mode="subprocess",
                command=["python", "-m", "elsewhere"],
                provenance=Provenance.HUMAN,
            ),
        )

        gateway.holds_the_lease = True
        await gateway.run_past_the_ttl()

        assert not gateway.orchestrator.is_tracking("mine")
        assert "mine" in gateway.repo.get_all_ids()


def _health_check_fails_for(gateway, variant: str) -> dict[str, bool]:
    """Fail validation of any definition whose command carries *variant*, until told otherwise."""
    real = gateway.orchestrator._validator.validate
    failing = {"on": True}

    async def validate(mcp_server, source=None, *, replacing=False):
        if failing["on"] and variant in " ".join(mcp_server.connection_info.get("command", [])):
            return ValidationReport(result=ValidationResult.FAILED_HEALTH, mcp_server=mcp_server, reason="starting")
        return await real(mcp_server, source, replacing=replacing)

    gateway.orchestrator._validator.validate = validate
    return failing


async def _regained_with_a_change_in_quarantine(durable):
    """A server whose changed definition waits in quarantine while the lease is lost and taken back."""
    gateway = durable.boot()
    gateway.source.names = {"s"}
    await gateway.tick(0)
    failing = _health_check_fails_for(gateway, "-v2")
    gateway.source.variant = "-v2"
    await gateway.tick()
    assert gateway.orchestrator._lifecycle_manager.is_quarantined("s")

    gateway.holds_the_lease = False
    await gateway.tick()
    gateway.holds_the_lease = True
    await gateway.tick()
    return gateway, failing


def _command_of(gateway, name: str) -> str:
    return " ".join(gateway.repo.get(name)._command)


@pytest.mark.asyncio
class TestAChangeInQuarantineWhenTheLeaseIsTakenBack:
    # Its row is the definition that runs, so it is tracked again: dropped and
    # skipped as quarantined, nothing governed it any more.
    async def test_the_running_server_is_still_tracked_and_counted(self, durable) -> None:
        gateway, _ = await _regained_with_a_change_in_quarantine(durable)

        assert gateway.orchestrator.is_tracking("s")
        assert gateway.orchestrator._validator._mcp_server_counts["filesystem"] == 1

    async def test_the_change_is_applied_once_it_passes(self, durable) -> None:
        gateway, failing = await _regained_with_a_change_in_quarantine(durable)

        failing["on"] = False
        await gateway.tick()

        assert "-v2" in _command_of(gateway, "s")

    async def test_approving_it_applies_it(self, durable) -> None:
        gateway, _ = await _regained_with_a_change_in_quarantine(durable)

        result = await gateway.orchestrator.approve_mcp_server("s")

        assert result["status"] == "updated"
        assert "-v2" in _command_of(gateway, "s")

    async def test_an_authoritative_source_that_drops_it_removes_it(self, durable) -> None:
        gateway, _ = await _regained_with_a_change_in_quarantine(durable)

        gateway.source.names = set()
        await gateway.run_past_the_ttl()

        assert "s" not in gateway.repo.get_all_ids()


def _server(name: str) -> DiscoveredMcpServer:
    return DiscoveredMcpServer.create(
        name=name,
        source_type="filesystem",
        mode="subprocess",
        connection_info={"command": ["python", "-m", name]},
        metadata={},
        ttl_seconds=TTL,
    )


class _Tracking:
    """An orchestrator without a fleet behind it, recording what it removes."""

    def __init__(self, holds: list[bool] | None = None) -> None:
        self.holds = holds
        self.orchestrator = DiscoveryOrchestrator(
            may_manage=(lambda: self.holds[0]) if holds is not None else None,
        )
        self.removed: list[str] = []

        async def deregister(name: str, _reason: str) -> None:
            self.removed.append(name)

        self.orchestrator._lifecycle_manager.on_deregister = deregister
        source = _Source(DiscoveryMode.AUTHORITATIVE)
        self.orchestrator.add_source(source)

    async def expiry_check(self) -> None:
        if self.orchestrator._lifecycle_manager._may_manage():
            await self.orchestrator._lifecycle_manager._check_expirations()


@pytest.mark.asyncio
class TestWithoutAFleetToTakeBack:
    async def test_a_standalone_gateway_removes_on_the_ttl_from_the_last_sighting(self, durable) -> None:
        gateway = _Tracking()
        await gateway.expiry_check()
        gateway.orchestrator._lifecycle_manager.add_mcp_server(_server("old"))

        _Clock.now += timedelta(seconds=TTL - 1)
        await gateway.expiry_check()
        assert gateway.removed == []

        _Clock.now += timedelta(seconds=2)
        await gateway.expiry_check()
        assert gateway.removed == ["old"]

    async def test_a_regained_lease_restarts_the_ttl_of_what_is_tracked(self, durable) -> None:
        gateway = _Tracking(holds=[True])
        await gateway.expiry_check()
        gateway.orchestrator._lifecycle_manager.add_mcp_server(_server("old"))

        gateway.holds[0] = False
        await gateway.expiry_check()
        _Clock.now += timedelta(seconds=TTL * 3)

        gateway.holds[0] = True
        await gateway.expiry_check()
        assert gateway.removed == []

        _Clock.now += timedelta(seconds=TTL + 1)
        await gateway.expiry_check()
        assert gateway.removed == ["old"]


@pytest.mark.asyncio
async def test_an_approval_that_reaches_the_loop_after_the_lease_is_lost_is_refused(durable) -> None:
    gateway, _ = await _regained_with_a_change_in_quarantine(durable)

    gateway.holds_the_lease = False
    with pytest.raises(McpServerNotHereError):
        await gateway.orchestrator._approve("s")

    assert gateway.orchestrator._lifecycle_manager.is_quarantined("s")
