"""A discovery approval applies the definition the operator reviewed, or nothing (#2109).

`approve_mcp_server(name)` applied whatever waited under the name when it ran. A
source re-reports its definition every cycle and overwrites the one waiting, so
a definition edited between the operator listing it and approving it was
applied unreviewed -- including, since #2091, a change that replaces a running
server. A change the source withdrew stayed approvable, and approval skipped
the command and source-policy checks every automatic registration passes.

An approval now names the fingerprint it reviewed and is refused (409) when the
definition waiting has another. One without a fingerprint still works, and says
in a warning that it is not bound. A withdrawn change is forgotten, and a
definition the checks refuse is not applied.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import structlog
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from mcp_hangar._sdk_compat import FastMCP
from mcp_hangar.application.discovery.discovery_orchestrator import DiscoveryConfig, DiscoveryOrchestrator
from mcp_hangar.domain.discovery.discovered_mcp_server import DiscoveredMcpServer
from mcp_hangar.domain.discovery.discovery_source import DiscoveryMode, DiscoverySource, SourcePolicyViolation
from mcp_hangar.domain.exceptions import DiscoveredDefinitionChangedError


class _Bus:
    def publish(self, event: Any) -> None:
        pass


class _Source(DiscoverySource):
    """Reports `defs`: name -> module, each run as `python -m <module>`."""

    def __init__(self) -> None:
        super().__init__(mode=DiscoveryMode.ADDITIVE)
        self.defs: dict[str, str] = {}
        self.refuse: set[str] = set()
        self.healthy = True

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
        return self.healthy

    def policy_violation(self, mcp_server: DiscoveredMcpServer) -> SourcePolicyViolation | None:
        if mcp_server.connection_info["command"][-1] in self.refuse:
            return SourcePolicyViolation(reason="this source refuses it")
        return None


class _CommandValidator:
    """An input validator refusing every command that runs `forbidden`."""

    def validate_command(self, command: list[str]) -> Any:
        if command[-1] == "forbidden":
            return SimpleNamespace(valid=False, issues=[SimpleNamespace(message="forbidden command")])
        return SimpleNamespace(valid=True, issues=[])


def _build(source: _Source, *, auto_register: bool = False, input_validator: Any = None) -> DiscoveryOrchestrator:
    config = DiscoveryConfig(auto_register=auto_register)
    config.security.require_health_check = False
    orchestrator = DiscoveryOrchestrator(config=config, event_bus=_Bus(), input_validator=input_validator)
    orchestrator.registered = []  # type: ignore[attr-defined]
    orchestrator.updated = []  # type: ignore[attr-defined]

    async def _register(server: DiscoveredMcpServer) -> bool:
        orchestrator.registered.append(server.connection_info["command"][-1])  # type: ignore[attr-defined]
        return True

    async def _update(server: DiscoveredMcpServer) -> None:
        orchestrator.updated.append(server.connection_info["command"][-1])  # type: ignore[attr-defined]

    orchestrator.on_register = _register
    orchestrator.on_update = _update
    orchestrator.add_source(source)
    return orchestrator


def _pending(orchestrator: DiscoveryOrchestrator) -> dict[str, str]:
    return {p.name: p.connection_info["command"][-1] for p in orchestrator.get_pending_mcp_servers()}


def _fingerprint(orchestrator: DiscoveryOrchestrator, name: str) -> str:
    return next(p.fingerprint for p in orchestrator.get_pending_mcp_servers() if p.name == name)


async def _running_with_a_pending_change(source: _Source, orchestrator: DiscoveryOrchestrator) -> None:
    source.defs["svc"] = "approved"
    await orchestrator.run_discovery_cycle()
    await orchestrator.approve_mcp_server("svc")
    source.defs["svc"] = "edited"
    await orchestrator.run_discovery_cycle()


@pytest.mark.asyncio
class TestAnApprovalBoundToAFingerprint:
    async def test_the_reviewed_definition_is_applied(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()

        result = await orchestrator.approve_mcp_server("svc", fingerprint=_fingerprint(orchestrator, "svc"))

        assert result == {"approved": True, "mcp_server": "svc", "status": "registered"}
        assert orchestrator.registered == ["reviewed"]
        assert _pending(orchestrator) == {}

    async def test_a_definition_changed_since_the_review_is_refused_and_nothing_changes(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()
        reviewed = _fingerprint(orchestrator, "svc")
        source.defs["svc"] = "edited"
        await orchestrator.run_discovery_cycle()
        current = _fingerprint(orchestrator, "svc")

        with pytest.raises(DiscoveredDefinitionChangedError) as refused:
            await orchestrator.approve_mcp_server("svc", fingerprint=reviewed)

        assert current in str(refused.value) and reviewed in str(refused.value)
        assert refused.value.details == {"reviewed_fingerprint": reviewed, "current_fingerprint": current}
        assert orchestrator.registered == []
        assert _pending(orchestrator) == {"svc": "edited"}

    async def test_a_change_of_a_running_server_edited_again_is_refused(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        await _running_with_a_pending_change(source, orchestrator)
        reviewed = _fingerprint(orchestrator, "svc")
        source.defs["svc"] = "edited-again"
        await orchestrator.run_discovery_cycle()

        with pytest.raises(DiscoveredDefinitionChangedError):
            await orchestrator.approve_mcp_server("svc", fingerprint=reviewed)

        assert orchestrator.updated == []
        assert orchestrator._lifecycle_manager.get_mcp_server("svc").connection_info["command"][-1] == "approved"
        assert _pending(orchestrator) == {"svc": "edited-again"}

    async def test_a_quarantined_definition_changed_since_the_review_is_refused(self) -> None:
        source = _Source()
        orchestrator = _build(source, auto_register=True)
        orchestrator._validator.config.max_registration_rate = 0
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()
        reviewed = orchestrator.get_quarantined()["svc"]["fingerprint"]
        source.defs["svc"] = "edited"
        await orchestrator.run_discovery_cycle()

        with pytest.raises(DiscoveredDefinitionChangedError):
            await orchestrator.approve_mcp_server("svc", fingerprint=reviewed)

        assert orchestrator.registered == []
        assert orchestrator.get_quarantined()["svc"]["mcp_server"]["connection_info"]["command"][-1] == "edited"


@pytest.mark.asyncio
class TestAnApprovalWithoutAFingerprint:
    async def test_it_still_applies_and_warns_that_it_is_not_bound(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()

        with structlog.testing.capture_logs() as captured:
            result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is True
        assert orchestrator.registered == ["reviewed"]
        warnings = [e for e in captured if e["event"] == "discovery_approval_not_bound"]
        assert len(warnings) == 1 and warnings[0]["log_level"] == "warning"
        assert warnings[0]["fingerprint"] == _fingerprint_of("reviewed")

    async def test_a_bound_approval_does_not_warn(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()

        with structlog.testing.capture_logs() as captured:
            await orchestrator.approve_mcp_server("svc", fingerprint=_fingerprint(orchestrator, "svc"))

        assert not [e for e in captured if e["event"] == "discovery_approval_not_bound"]


@pytest.mark.asyncio
class TestAWithdrawnChangeCannotBeApproved:
    async def test_a_source_back_on_the_running_definition_clears_the_pending_change(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        await _running_with_a_pending_change(source, orchestrator)
        assert _pending(orchestrator) == {"svc": "edited"}

        source.defs["svc"] = "approved"
        await orchestrator.run_discovery_cycle()
        result = await orchestrator.approve_mcp_server("svc")

        assert _pending(orchestrator) == {}
        assert result["approved"] is False
        assert orchestrator.updated == []

    async def test_a_source_that_stops_reporting_it_clears_the_pending_entry(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "new"
        await orchestrator.run_discovery_cycle()

        del source.defs["svc"]
        await orchestrator.run_discovery_cycle()
        result = await orchestrator.approve_mcp_server("svc")

        assert _pending(orchestrator) == {}
        assert result["approved"] is False
        assert orchestrator.registered == []

    async def test_a_source_that_is_not_healthy_has_withdrawn_nothing(self) -> None:
        # A source that cannot reach what it watches answers an empty list.
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "new"
        await orchestrator.run_discovery_cycle()

        del source.defs["svc"]
        source.healthy = False
        await orchestrator.run_discovery_cycle()

        assert _pending(orchestrator) == {"svc": "new"}

    async def test_a_health_check_that_raises_keeps_the_entry_and_the_scan(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "new"
        await orchestrator.run_discovery_cycle()

        async def _raises() -> bool:
            raise ConnectionError("unreachable")

        source.health_check = _raises  # type: ignore[method-assign]
        source.defs = {"other": "other"}
        result = await orchestrator.run_discovery_cycle()

        assert result.error_count == 0
        assert _pending(orchestrator) == {"svc": "new", "other": "other"}

    async def test_a_removed_source_leaves_nothing_to_approve(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "new"
        await orchestrator.run_discovery_cycle()
        orchestrator._lifecycle_manager.quarantine(
            DiscoveredMcpServer.create(
                name="held",
                source_type="filesystem",
                mode="subprocess",
                connection_info={"command": ["python", "-m", "held"]},
                metadata={},
            ),
            "failed validation",
        )

        orchestrator.remove_source("filesystem")

        assert (await orchestrator.approve_mcp_server("svc"))["approved"] is False
        assert (await orchestrator.approve_mcp_server("held"))["approved"] is False
        assert orchestrator.registered == []

    async def test_a_source_back_on_the_running_definition_clears_a_quarantined_change(self) -> None:
        source = _Source()
        orchestrator = _build(source, auto_register=True)
        source.defs["svc"] = "running"
        await orchestrator.run_discovery_cycle()
        orchestrator._validator.config.max_registration_rate = 0
        source.defs["svc"] = "edited"
        await orchestrator.run_discovery_cycle()
        assert "svc" in orchestrator.get_quarantined()

        source.defs["svc"] = "running"
        await orchestrator.run_discovery_cycle()
        result = await orchestrator.approve_mcp_server("svc")

        assert orchestrator.get_quarantined() == {}
        assert result["approved"] is False
        assert orchestrator.updated == []


@pytest.mark.asyncio
class TestAnApprovalRunsTheChecksARegistrationRuns:
    async def test_a_definition_the_source_policy_refuses_is_not_applied(self) -> None:
        source = _Source()
        source.refuse.add("refused")
        orchestrator = _build(source)
        source.defs["svc"] = "refused"
        await orchestrator.run_discovery_cycle()

        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is False
        assert "this source refuses it" in result["error"]
        assert orchestrator.registered == []
        # Refused, not consumed: it waits where it was, for a reject.
        assert _pending(orchestrator) == {"svc": "refused"}

    async def test_a_change_the_source_policy_refuses_leaves_the_running_definition(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        await _running_with_a_pending_change(source, orchestrator)
        source.refuse.add("edited")

        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is False
        assert orchestrator.updated == []
        assert orchestrator._lifecycle_manager.get_mcp_server("svc").connection_info["command"][-1] == "approved"

    async def test_a_command_the_input_validator_refuses_is_not_applied(self) -> None:
        source = _Source()
        orchestrator = _build(source, input_validator=_CommandValidator())
        source.defs["svc"] = "forbidden"
        await orchestrator.run_discovery_cycle()

        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is False
        assert "forbidden command" in result["error"]
        assert orchestrator.registered == []


@pytest.fixture
def waiting_orchestrator():
    """An orchestrator with one pending and one quarantined definition."""

    async def _make() -> DiscoveryOrchestrator:
        source = _Source()
        orchestrator = _build(source)
        source.defs["pending-one"] = "pending"
        await orchestrator.run_discovery_cycle()
        quarantined = DiscoveredMcpServer.create(
            name="quarantined-one",
            source_type="filesystem",
            mode="subprocess",
            connection_info={"command": ["python", "-m", "quarantined"]},
            metadata={},
        )
        orchestrator._lifecycle_manager.quarantine(quarantined, "failed validation")
        return orchestrator

    return _make


def _context(orchestrator: DiscoveryOrchestrator) -> MagicMock:
    ctx = MagicMock()
    ctx.discovery_orchestrator = orchestrator
    ctx.rate_limiter.consume.return_value = MagicMock(allowed=True)
    return ctx


@pytest.mark.asyncio
class TestTheListingsShowTheFingerprint:
    async def test_the_tools_list_it_and_the_approve_tool_takes_it(self, waiting_orchestrator) -> None:
        from mcp_hangar.server.tools.discovery import register_discovery_tools

        orchestrator = await waiting_orchestrator()
        mcp = FastMCP("test")
        register_discovery_tools(mcp)
        ctx = _context(orchestrator)
        with (
            patch("mcp_hangar.server.tools.discovery.get_context", return_value=ctx),
            patch("mcp_hangar.server.validation.get_context", return_value=ctx),
        ):
            discovered = json.loads((await mcp.call_tool("hangar_discovered", {})).content[0].text)
            quarantined = json.loads((await mcp.call_tool("hangar_quarantine", {})).content[0].text)
            stale = await mcp.call_tool("hangar_approve", {"mcp_server": "pending-one", "fingerprint": "0" * 16})
            pending_fp = discovered["pending"][0]["fingerprint"]
            approved = await mcp.call_tool("hangar_approve", {"mcp_server": "pending-one", "fingerprint": pending_fp})

        assert pending_fp == _fingerprint_of("pending")
        assert quarantined["quarantined"][0]["fingerprint"] == _q()
        refusal = json.loads(stale.content[0].text)
        assert refusal["error_type"] == "DiscoveredDefinitionChangedError"
        assert pending_fp in refusal["error"]
        assert json.loads(approved.content[0].text)["approved"] is True

    async def test_the_rest_listings_show_it_and_a_stale_approval_answers_409(self, waiting_orchestrator) -> None:
        from mcp_hangar.server.api import create_api_router

        orchestrator = await waiting_orchestrator()
        ctx = _context(orchestrator)
        with (
            patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
            patch("mcp_hangar.server.api.discovery.get_context", return_value=ctx),
        ):
            client = TestClient(Starlette(routes=[Mount("/api", app=create_api_router())]))
            pending = client.get("/api/discovery/pending").json()["pending"]
            quarantined = client.get("/api/discovery/quarantined").json()["quarantined"]
            stale = client.post("/api/discovery/approve/pending-one", json={"fingerprint": "0" * 16})
            stale_query = client.post("/api/discovery/approve/pending-one?fingerprint=" + "0" * 16)
            invalid = client.post("/api/discovery/approve/pending-one", json={"fingerprint": 7})
            approved = client.post("/api/discovery/approve/quarantined-one", json={"fingerprint": _q()})

        assert pending[0]["fingerprint"] == _fingerprint_of("pending")
        assert quarantined["quarantined-one"]["fingerprint"] == _q()
        assert stale.status_code == 409 and stale_query.status_code == 409
        error = stale.json()["error"]
        assert error["code"] == "DiscoveredDefinitionChangedError"
        assert error["details"]["current_fingerprint"] == _fingerprint_of("pending")
        assert invalid.status_code == 400
        assert approved.status_code == 200 and approved.json()["approved"] is True
        assert [p.name for p in orchestrator.get_pending_mcp_servers()] == ["pending-one"]

    async def test_a_rest_approval_with_no_body_still_works(self, waiting_orchestrator) -> None:
        from mcp_hangar.server.api import create_api_router

        orchestrator = await waiting_orchestrator()
        ctx = _context(orchestrator)
        with (
            patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
            patch("mcp_hangar.server.api.discovery.get_context", return_value=ctx),
        ):
            client = TestClient(Starlette(routes=[Mount("/api", app=create_api_router())]))
            response = client.post("/api/discovery/approve/pending-one")

        assert response.status_code == 200 and response.json()["approved"] is True


def _fingerprint_of(module: str) -> str:
    """A definition's fingerprint: of its connection and metadata, not of its name."""
    return DiscoveredMcpServer.create(
        name="any",
        source_type="filesystem",
        mode="subprocess",
        connection_info={"command": ["python", "-m", module]},
        metadata={},
    ).fingerprint


def _q() -> str:
    return _fingerprint_of("quarantined")
