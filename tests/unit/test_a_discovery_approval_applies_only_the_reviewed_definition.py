"""A discovery approval applies the definition the operator reviewed, or nothing (#2109).

`approve_mcp_server(name)` applied whatever waited under the name when it ran. A
source re-reports its definition every cycle and overwrites the one waiting, so
a definition edited between the operator listing it and approving it was
applied unreviewed -- including, since #2091, a change that replaces a running
server. A change the source withdrew stayed approvable, and approval skipped
the command and source-policy checks every automatic registration passes.

An approval now names the `definition_digest` it reviewed -- the full SHA-256 of
the definition, not the 64-bit fingerprint a source could collide -- and is
refused (409) when the definition waiting has another. One without a digest
still works, and says in a warning that it is not bound. A withdrawn change is forgotten, and a
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
        #: Further (name, module) copies reported in the same scan.
        self.copies: list[tuple[str, str]] = []
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
            for name, module in [*sorted(self.defs.items()), *self.copies]
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


def _digest(orchestrator: DiscoveryOrchestrator, name: str) -> str:
    return next(p.definition_digest for p in orchestrator.get_pending_mcp_servers() if p.name == name)


async def _running_with_a_pending_change(source: _Source, orchestrator: DiscoveryOrchestrator) -> None:
    source.defs["svc"] = "approved"
    await orchestrator.run_discovery_cycle()
    await orchestrator.approve_mcp_server("svc")
    source.defs["svc"] = "edited"
    await orchestrator.run_discovery_cycle()


@pytest.mark.asyncio
class TestAnApprovalBoundToADigest:
    async def test_the_reviewed_definition_is_applied(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()

        result = await orchestrator.approve_mcp_server("svc", definition_digest=_digest(orchestrator, "svc"))

        assert result == {"approved": True, "mcp_server": "svc", "status": "registered"}
        assert orchestrator.registered == ["reviewed"]
        assert _pending(orchestrator) == {}

    async def test_a_definition_changed_since_the_review_is_refused_and_nothing_changes(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()
        reviewed = _digest(orchestrator, "svc")
        source.defs["svc"] = "edited"
        await orchestrator.run_discovery_cycle()
        current = _digest(orchestrator, "svc")

        with pytest.raises(DiscoveredDefinitionChangedError) as refused:
            await orchestrator.approve_mcp_server("svc", definition_digest=reviewed)

        assert current in str(refused.value) and reviewed in str(refused.value)
        assert refused.value.details == {"reviewed_definition_digest": reviewed, "current_definition_digest": current}
        assert orchestrator.registered == []
        assert _pending(orchestrator) == {"svc": "edited"}

    async def test_the_short_fingerprint_does_not_bind(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()
        fingerprint = orchestrator.get_pending_mcp_servers()[0].fingerprint

        with pytest.raises(DiscoveredDefinitionChangedError):
            await orchestrator.approve_mcp_server("svc", definition_digest=fingerprint)

        assert orchestrator.registered == []

    async def test_the_digest_covers_what_the_fingerprint_leaves_out(self) -> None:
        # The fingerprint covers connection and metadata only; the digest also
        # covers the mode and the source, at full length.
        one = DiscoveredMcpServer.create("svc", "filesystem", "subprocess", {"command": ["x"]}, {})
        other_mode = DiscoveredMcpServer.create("svc", "filesystem", "stdio", {"command": ["x"]}, {})
        other_source = DiscoveredMcpServer.create("svc", "docker", "subprocess", {"command": ["x"]}, {})

        assert one.fingerprint == other_mode.fingerprint == other_source.fingerprint
        assert len({one.definition_digest, other_mode.definition_digest, other_source.definition_digest}) == 3
        assert len(one.definition_digest) == 64

    async def test_a_change_of_a_running_server_edited_again_is_refused(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        await _running_with_a_pending_change(source, orchestrator)
        reviewed = _digest(orchestrator, "svc")
        source.defs["svc"] = "edited-again"
        await orchestrator.run_discovery_cycle()

        with pytest.raises(DiscoveredDefinitionChangedError):
            await orchestrator.approve_mcp_server("svc", definition_digest=reviewed)

        assert orchestrator.updated == []
        assert orchestrator._lifecycle_manager.get_mcp_server("svc").connection_info["command"][-1] == "approved"
        assert _pending(orchestrator) == {"svc": "edited-again"}

    async def test_a_quarantined_definition_changed_since_the_review_is_refused(self) -> None:
        source = _Source()
        orchestrator = _build(source, auto_register=True)
        orchestrator._validator.config.max_registration_rate = 0
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()
        reviewed = orchestrator.get_quarantined()["svc"]["definition_digest"]
        source.defs["svc"] = "edited"
        await orchestrator.run_discovery_cycle()

        with pytest.raises(DiscoveredDefinitionChangedError):
            await orchestrator.approve_mcp_server("svc", definition_digest=reviewed)

        assert orchestrator.registered == []
        assert orchestrator.get_quarantined()["svc"]["mcp_server"]["connection_info"]["command"][-1] == "edited"


@pytest.mark.asyncio
class TestAnApprovalWithoutADigest:
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
        assert warnings[0]["definition_digest"] == _digest_of("reviewed")

    async def test_a_bound_approval_does_not_warn(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "reviewed"
        await orchestrator.run_discovery_cycle()

        with structlog.testing.capture_logs() as captured:
            await orchestrator.approve_mcp_server("svc", definition_digest=_digest(orchestrator, "svc"))

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

    async def test_a_pending_change_reported_beside_the_running_definition_stays(self) -> None:
        # A rolling update: old and new pods side by side, one name twice in a scan.
        source = _Source()
        orchestrator = _build(source)
        await _running_with_a_pending_change(source, orchestrator)

        for copies in ([("svc", "approved")], [("svc", "approved")]):
            source.copies = copies
            await orchestrator.run_discovery_cycle()

        assert _pending(orchestrator) == {"svc": "edited"}
        source.copies = []
        source.defs["svc"] = "approved"
        await orchestrator.run_discovery_cycle()
        assert _pending(orchestrator) == {}

    async def test_an_approval_during_the_health_check_does_not_break_the_cycle(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        source.defs["svc"] = "new"
        await orchestrator.run_discovery_cycle()
        approvals: list[dict[str, Any]] = []

        async def _approve_meanwhile() -> bool:
            approvals.append(await orchestrator.approve_mcp_server("svc"))
            return True

        source.health_check = _approve_meanwhile  # type: ignore[method-assign]
        del source.defs["svc"]
        result = await orchestrator.run_discovery_cycle()

        assert result.error_count == 0
        assert approvals[0]["approved"] is True
        assert _pending(orchestrator) == {}

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

    async def test_a_definition_whose_source_is_not_registered_is_refused(self) -> None:
        source = _Source()
        orchestrator = _build(source)
        orchestrator._discovery_service._pending["svc"] = DiscoveredMcpServer.create(
            "svc", "elsewhere", "subprocess", {"command": ["python", "-m", "unchecked"]}, {}
        )

        result = await orchestrator.approve_mcp_server("svc")

        assert result["approved"] is False
        assert "not registered" in result["error"]
        assert orchestrator.registered == []

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


def _rest(orchestrator: DiscoveryOrchestrator):
    from mcp_hangar.server.api import create_api_router

    ctx = _context(orchestrator)
    patches = (
        patch("mcp_hangar.server.api.middleware.get_context", return_value=ctx),
        patch("mcp_hangar.server.api.discovery.get_context", return_value=ctx),
    )
    return patches, TestClient(Starlette(routes=[Mount("/api", app=create_api_router())]))


STALE = "0" * 64


@pytest.mark.asyncio
class TestTheListingsShowTheDigest:
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
            stale = await mcp.call_tool("hangar_approve", {"mcp_server": "pending-one", "definition_digest": STALE})
            digest = discovered["pending"][0]["definition_digest"]
            approved = await mcp.call_tool("hangar_approve", {"mcp_server": "pending-one", "definition_digest": digest})

        assert digest == _digest_of("pending")
        assert quarantined["quarantined"][0]["definition_digest"] == _digest_of("quarantined")
        refusal = json.loads(stale.content[0].text)
        assert refusal["error_type"] == "DiscoveredDefinitionChangedError"
        assert digest in refusal["error"]
        assert json.loads(approved.content[0].text)["approved"] is True

    @pytest.mark.parametrize("value", ["abc123", "", "A" * 64])
    async def test_the_approve_tool_refuses_what_is_not_a_digest(self, waiting_orchestrator, value: str) -> None:
        from mcp_hangar.server.tools.discovery import register_discovery_tools

        orchestrator = await waiting_orchestrator()
        mcp = FastMCP("test")
        register_discovery_tools(mcp)
        ctx = _context(orchestrator)
        with (
            patch("mcp_hangar.server.tools.discovery.get_context", return_value=ctx),
            patch("mcp_hangar.server.validation.get_context", return_value=ctx),
        ):
            try:
                answer = await mcp.call_tool(
                    "hangar_approve", {"mcp_server": "pending-one", "definition_digest": value}
                )
                refused = answer.is_error or "invalid_definition_digest" in answer.content[0].text
            except Exception as e:  # noqa: BLE001 -- an invalid argument may surface as a raised error
                refused = "invalid_definition_digest" in str(e)

        assert refused
        assert orchestrator.registered == []

    async def test_the_rest_listings_show_it_and_a_stale_approval_answers_409(self, waiting_orchestrator) -> None:
        orchestrator = await waiting_orchestrator()
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            pending = client.get("/api/discovery/pending").json()["pending"]
            quarantined = client.get("/api/discovery/quarantined").json()["quarantined"]
            stale = client.post("/api/discovery/approve/pending-one", json={"definition_digest": STALE})
            stale_query = client.post(f"/api/discovery/approve/pending-one?definition_digest={STALE}")
            approved = client.post(
                "/api/discovery/approve/quarantined-one", json={"definition_digest": _digest_of("quarantined")}
            )

        assert pending[0]["definition_digest"] == _digest_of("pending")
        assert quarantined["quarantined-one"]["definition_digest"] == _digest_of("quarantined")
        assert stale.status_code == 409 and stale_query.status_code == 409
        error = stale.json()["error"]
        assert error["code"] == "DiscoveredDefinitionChangedError"
        assert error["details"]["current_definition_digest"] == _digest_of("pending")
        assert approved.status_code == 200 and approved.json()["approved"] is True
        assert [p.name for p in orchestrator.get_pending_mcp_servers()] == ["pending-one"]

    async def test_a_rest_approval_with_no_body_still_works(self, waiting_orchestrator) -> None:
        orchestrator = await waiting_orchestrator()
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            response = client.post("/api/discovery/approve/pending-one")

        assert response.status_code == 200 and response.json()["approved"] is True


@pytest.mark.asyncio
class TestARestApprovalThatMightHaveMeantToBindIsRefused:
    """Every probe used to fall through to an unbound approval and answer 200."""

    @pytest.mark.parametrize(
        ("kwargs", "error"),
        [
            ({"content": b"{not json", "headers": {"content-type": "application/json"}}, "invalid_body"),
            ({"json": [STALE]}, "invalid_body"),
            ({"json": "x"}, "invalid_body"),
            ({"data": {"definition_digest": STALE}}, "invalid_body"),
            ({"json": {"Definition_Digest": STALE}}, "unknown_fields"),
            ({"json": {"fingerprint": "abc123"}}, "unknown_fields"),
            ({"json": {"definition_digest": None}}, "invalid_definition_digest"),
            ({"json": {"definition_digest": 7}}, "invalid_definition_digest"),
            ({"json": {"definition_digest": ""}}, "invalid_definition_digest"),
            ({"json": {"definition_digest": "abc123"}}, "invalid_definition_digest"),
            ({"params": {"fingerprint": "abc123"}}, "unknown_fields"),
            ({"params": {"Definition_Digest": STALE}}, "unknown_fields"),
            ({"params": {"definition-digest": STALE}}, "unknown_fields"),
            ({"params": {"DefinitionDigest": STALE}}, "unknown_fields"),
            ({"params": {"definition_digest": ""}}, "invalid_definition_digest"),
            (
                {"params": {"definition_digest": STALE}, "json": {"definition_digest": "1" * 64}},
                "conflicting_definition_digest",
            ),
        ],
    )
    async def test_it_answers_400_and_changes_nothing(self, waiting_orchestrator, kwargs, error) -> None:
        orchestrator = await waiting_orchestrator()
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            response = client.post("/api/discovery/approve/pending-one", **kwargs)

        assert response.status_code == 400, response.text
        assert response.json()["error"] == error
        assert orchestrator.registered == []
        assert [p.name for p in orchestrator.get_pending_mcp_servers()] == ["pending-one"]

    async def test_an_unrelated_query_parameter_is_ignored(self, waiting_orchestrator) -> None:
        orchestrator = await waiting_orchestrator()
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            response = client.post("/api/discovery/approve/pending-one?_=123&traceparent=00-x-y-01")

        assert response.status_code == 200 and response.json()["approved"] is True

    async def test_query_and_body_that_agree_bind(self, waiting_orchestrator) -> None:
        orchestrator = await waiting_orchestrator()
        digest = _digest_of("pending")
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            response = client.post(
                f"/api/discovery/approve/pending-one?definition_digest={digest}", json={"definition_digest": digest}
            )

        assert response.status_code == 200 and response.json()["approved"] is True

    async def test_an_empty_json_object_is_an_unbound_approval(self, waiting_orchestrator) -> None:
        orchestrator = await waiting_orchestrator()
        patches, client = _rest(orchestrator)
        with patches[0], patches[1]:
            response = client.post("/api/discovery/approve/pending-one", json={})

        assert response.status_code == 200 and response.json()["approved"] is True


def _digest_of(module: str) -> str:
    """The digest of a definition this file's source reports for *module*."""
    return DiscoveredMcpServer.create(
        name="any",
        source_type="filesystem",
        mode="subprocess",
        connection_info={"command": ["python", "-m", module]},
        metadata={},
    ).definition_digest
