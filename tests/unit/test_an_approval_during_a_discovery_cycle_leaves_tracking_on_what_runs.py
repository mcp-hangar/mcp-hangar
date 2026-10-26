"""An approval during a discovery cycle leaves tracking on what runs (#2129).

A cycle processing a server read the tracked definition, awaited validation,
and then tracked what it had read. An approval landing during that await
applied the quarantined change, and the cycle, refusing what its source now
reported, put the old definition back in tracking: the control plane ran the
approved change while discovery tracked the definition it replaced. When the
source went back to that old definition it read as unchanged, so it was never
applied again and the approved change ran indefinitely.

An approval and a cycle on the same name now take turns: the approval waits for
the cycle to finish and then sees what it left. Different names do not wait for
each other. TTL expiry and a tenure rebuild do not wait for the cycle; a cycle
that finds tracking changed under it writes nothing back.

These drive the real orchestrator, service and lifecycle manager. Only the
security validator is replaced, by one whose verdict the test sets and whose
await the test holds open.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.application.discovery.security_validator import ValidationReport, ValidationResult
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource
from mcp_hangar.domain.exceptions import DiscoveredDefinitionChangedError

WAIT = 2


class _Bus:
    def publish(self, event: Any) -> None:
        pass


class _Source(DiscoverySource):
    """Reports `defs`: name -> module, each run as `python -m <module>`."""

    def __init__(self) -> None:
        super().__init__(mode=DiscoveryMode.ADDITIVE)
        self.defs: dict[str, str] = {}

    @property
    def source_type(self) -> str:
        return "filesystem"

    async def discover(self) -> list[DiscoveredMcpServer]:
        return [
            DiscoveredMcpServer.create(
                name=name,
                source_type="filesystem",
                mode="subprocess",
                connection_info={"command": ["python", "-m", module]},
                metadata={},
            )
            for name, module in sorted(self.defs.items())
        ]

    async def health_check(self) -> bool:
        return True


class _Validator:
    """Fails the modules in `failing`; holds validation of `held` until `release` is set."""

    def __init__(self) -> None:
        self.failing: set[str] = set()
        self.held: str | None = None
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def validate(self, mcp_server: DiscoveredMcpServer, source: Any = None, *, replacing: bool = False) -> Any:
        module = _module(mcp_server)
        if module == self.held:
            self.entered.set()
            await self.release.wait()
        if module in self.failing:
            return ValidationReport(result=ValidationResult.FAILED_HEALTH, mcp_server=mcp_server, reason="unhealthy")
        return ValidationReport(result=ValidationResult.PASSED, mcp_server=mcp_server, reason="ok")


def _module(mcp_server: DiscoveredMcpServer) -> str:
    return mcp_server.connection_info["command"][-1]


def _build(source: _Source) -> tuple[DiscoveryOrchestrator, _Validator, dict[str, str]]:
    """An orchestrator, its validator, and what the control plane runs: name -> module."""
    config = DiscoveryConfig(auto_register=True)
    orchestrator = DiscoveryOrchestrator(config=config, event_bus=_Bus())
    validator = _Validator()
    orchestrator._validator.validate = validator.validate  # type: ignore[method-assign]
    running: dict[str, str] = {}

    async def _register(server: DiscoveredMcpServer) -> bool:
        running[server.name] = _module(server)
        return True

    async def _update(server: DiscoveredMcpServer) -> None:
        running[server.name] = _module(server)

    orchestrator.on_register = _register
    orchestrator.on_update = _update
    orchestrator.add_source(source)
    return orchestrator, validator, running


def _tracked(orchestrator: DiscoveryOrchestrator, name: str) -> str | None:
    server = orchestrator._lifecycle_manager.get_mcp_server(name)
    return None if server is None else _module(server)


def _digest(orchestrator: DiscoveryOrchestrator, name: str) -> str:
    return orchestrator.get_quarantined()[name]["definition_digest"]


async def _running_a_with_b_quarantined(
    source: _Source, orchestrator: DiscoveryOrchestrator, validator: _Validator, name: str = "svc"
) -> None:
    source.defs[name] = "a"
    await orchestrator.run_discovery_cycle()
    validator.failing.add("b")
    source.defs[name] = "b"
    await orchestrator.run_discovery_cycle()
    assert orchestrator.get_quarantined()[name]["mcp_server"]["connection_info"]["command"][-1] == "b"


async def _yield(times: int = 20) -> None:
    for _ in range(times):
        await asyncio.sleep(0)


@pytest.mark.asyncio
class TestAnApprovalDuringACycle:
    async def test_tracking_matches_what_runs_and_a_revert_is_applied(self) -> None:
        source = _Source()
        orchestrator, validator, running = _build(source)
        await _running_a_with_b_quarantined(source, orchestrator, validator)
        reviewed = _digest(orchestrator, "svc")

        # The source now reports c, and c fails too; its validation is held open.
        validator.failing.add("c")
        validator.held = "c"
        source.defs["svc"] = "c"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        try:
            await asyncio.wait_for(validator.entered.wait(), WAIT)
            approval = asyncio.create_task(orchestrator.approve_mcp_server("svc", definition_digest=reviewed))
            await _yield()
        finally:
            validator.release.set()
        await asyncio.wait_for(cycle, WAIT)
        (outcome,) = await asyncio.wait_for(asyncio.gather(approval, return_exceptions=True), WAIT)

        assert _tracked(orchestrator, "svc") == running["svc"]
        # The approval waited for the cycle, which put c where b waited: the
        # definition reviewed is no longer the one waiting, so nothing changed.
        assert isinstance(outcome, DiscoveredDefinitionChangedError)
        assert running["svc"] == "a"

        validator.held = None
        validator.failing.discard("b")
        source.defs["svc"] = "b"
        await orchestrator.run_discovery_cycle()
        assert running["svc"] == _tracked(orchestrator, "svc") == "b"

        source.defs["svc"] = "a"
        await orchestrator.run_discovery_cycle()
        assert running["svc"] == _tracked(orchestrator, "svc") == "a"

    async def test_an_approval_that_waited_for_the_cycle_completes_after_it(self) -> None:
        source = _Source()
        orchestrator, validator, running = _build(source)
        await _running_a_with_b_quarantined(source, orchestrator, validator)
        reviewed = _digest(orchestrator, "svc")

        # The source still reports b, which fails again; its validation is held open.
        validator.held = "b"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        try:
            await asyncio.wait_for(validator.entered.wait(), WAIT)
            approval = asyncio.create_task(orchestrator.approve_mcp_server("svc", definition_digest=reviewed))
            await _yield()
            assert not approval.done()
            assert running["svc"] == "a"
        finally:
            validator.release.set()
        await asyncio.wait_for(cycle, WAIT)
        result = await asyncio.wait_for(approval, WAIT)

        assert result == {"approved": True, "mcp_server": "svc", "status": "updated"}
        assert running["svc"] == _tracked(orchestrator, "svc") == "b"

        # The source goes back to a, which differs from what runs, and is applied.
        validator.held = None
        source.defs["svc"] = "a"
        await orchestrator.run_discovery_cycle()
        assert running["svc"] == _tracked(orchestrator, "svc") == "a"

    async def test_an_approval_of_another_name_does_not_wait(self) -> None:
        source = _Source()
        orchestrator, validator, running = _build(source)
        await _running_a_with_b_quarantined(source, orchestrator, validator, name="svc")
        reviewed = _digest(orchestrator, "svc")

        # Another server, processed first in the cycle, is held in validation.
        validator.held = "other"
        source.defs["early"] = "other"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        try:
            await asyncio.wait_for(validator.entered.wait(), WAIT)
            result = await asyncio.wait_for(orchestrator.approve_mcp_server("svc", definition_digest=reviewed), WAIT)
            assert not cycle.done()
        finally:
            validator.release.set()
        await asyncio.wait_for(cycle, WAIT)

        assert result == {"approved": True, "mcp_server": "svc", "status": "updated"}
        assert running == {"svc": "b", "early": "other"}
        assert _tracked(orchestrator, "svc") == "b"
        assert orchestrator._name_locks == {}


def _discovered(name: str, module: str) -> DiscoveredMcpServer:
    return DiscoveredMcpServer.create(
        name=name,
        source_type="filesystem",
        mode="subprocess",
        connection_info={"command": ["python", "-m", module]},
        metadata={},
    )


@pytest.mark.asyncio
class TestTrackingThatChangesDuringValidation:
    """TTL expiry and a tenure rebuild do not take the name's lock; a cycle that sees either writes nothing back."""

    async def test_a_deregistration_while_validating_leaves_nothing_tracked_until_reported_again(self) -> None:
        source = _Source()
        orchestrator, validator, running = _build(source)

        async def _deregister(name: str, reason: str) -> None:
            running.pop(name, None)

        orchestrator.on_deregister = _deregister
        # Wired by start(), which would also start the background loops.
        orchestrator._lifecycle_manager.on_deregister = orchestrator._handle_deregister
        source.defs["svc"] = "a"
        await orchestrator.run_discovery_cycle()

        validator.failing.add("c")
        validator.held = "c"
        source.defs["svc"] = "c"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        try:
            await asyncio.wait_for(validator.entered.wait(), WAIT)
            await orchestrator._lifecycle_manager._deregister("svc", "ttl_expired")
        finally:
            validator.release.set()
        await asyncio.wait_for(cycle, WAIT)

        assert _tracked(orchestrator, "svc") is None
        assert "svc" not in running

        validator.held = None
        validator.failing.clear()
        await orchestrator.run_discovery_cycle()
        assert running["svc"] == _tracked(orchestrator, "svc") == "c"

    async def test_a_tenure_rebuild_while_validating_is_not_overwritten(self) -> None:
        source = _Source()
        holds = True
        fleet: list[DiscoveredMcpServer] = []
        config = DiscoveryConfig(auto_register=True)
        orchestrator = DiscoveryOrchestrator(config=config, event_bus=_Bus(), may_manage=lambda: holds)
        validator = _Validator()
        orchestrator._validator.validate = validator.validate  # type: ignore[method-assign]

        async def _register(server: DiscoveredMcpServer) -> bool:
            return True

        async def _update(server: DiscoveredMcpServer) -> None:
            pass

        orchestrator.on_register = _register
        orchestrator.on_update = _update
        orchestrator.restored_servers = lambda: list(fleet)
        orchestrator.add_source(source)
        source.defs["svc"] = "a"
        await orchestrator.run_discovery_cycle()

        validator.failing.add("c")
        validator.held = "c"
        source.defs["svc"] = "c"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        try:
            await asyncio.wait_for(validator.entered.wait(), WAIT)
            # The lease is lost and regained; meanwhile a peer applied b.
            fleet.append(_discovered("svc", "b"))
            holds = False
            orchestrator._manages()
            holds = True
            orchestrator._manages()
            assert _tracked(orchestrator, "svc") == "b"
        finally:
            validator.release.set()
        await asyncio.wait_for(cycle, WAIT)

        assert _tracked(orchestrator, "svc") == "b"

    async def test_a_cycle_cancelled_while_an_approval_waits_leaves_no_lock_behind(self) -> None:
        source = _Source()
        orchestrator, validator, running = _build(source)
        await _running_a_with_b_quarantined(source, orchestrator, validator)
        reviewed = _digest(orchestrator, "svc")

        validator.held = "b"
        cycle = asyncio.create_task(orchestrator.run_discovery_cycle())
        await asyncio.wait_for(validator.entered.wait(), WAIT)
        approval = asyncio.create_task(orchestrator.approve_mcp_server("svc", definition_digest=reviewed))
        await _yield()
        cycle.cancel()
        with pytest.raises(asyncio.CancelledError):
            await cycle
        result = await asyncio.wait_for(approval, WAIT)

        assert result == {"approved": True, "mcp_server": "svc", "status": "updated"}
        assert running["svc"] == _tracked(orchestrator, "svc") == "b"
        assert orchestrator._name_locks == {}
