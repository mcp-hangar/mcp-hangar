"""A refused tool call leaves exactly one audit record, with bounded fields only (#1582).

A refusal returns before the aggregate invokes anything, so neither
``ToolInvocationCompleted`` nor ``ToolInvocationFailed`` existed for it, and the
audit handler -- which exported only those -- never saw a governance decision.
Each refusal now publishes one ``ToolCallRefused``: every ``_GATES`` stage that
says ``deny``, the ``tool:invoke`` check before the gates, and an L7 verdict
raised at dispatch. Audit exports it as ``tool_invocation`` with
``status=denied``, the caller, the tenant and the ADR-029 decision fields.

Every case plants text from outside the gate where the gate takes some (the
approver's reason, the validator's), and asserts the record holds none of it,
nor the refusal message the caller is told.
"""

from __future__ import annotations

import time
from contextlib import ExitStack
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

import pytest

from mcp_hangar.application.event_handlers.audit_event_handler import OTLPAuditEventHandler
from mcp_hangar.domain.events import ToolCallRefused
from mcp_hangar.domain.exceptions import AccessDeniedError
from mcp_hangar.domain.policies.egress_l7 import ToolRules
from mcp_hangar.infrastructure.observability.otlp_audit_exporter import OTLPAuditExporter
from mcp_hangar.observability.conventions import L7, MCP, Caller, Gate, GenAI, McpServer
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec, _authorize_calls
from mcp_hangar.server.tools.batch.executor import _GATES, _publish_gate_refusal
from mcp_hangar.server.tools.batch.models import CallResult
from mcp_hangar.server.tools.batch.tenant_admission import Refusal
from tests.unit import test_l7_verdicts_on_spans as l7_cases
from tests.unit import test_refusal_log_carries_no_free_text as gate_cases
from tests.unit.test_batch_gate_precedence import _SERVER, _TENANT, _TOOL, _arrange_catalogue, _identity, _run

ctx = gate_cases.ctx
_reset_singletons = gate_cases._reset_singletons
observer = l7_cases.observer
CANARY = gate_cases.CANARY

#: Every attribute a refusal's record may carry. Anything else is a leak.
_ALLOWED = {
    "mcp.event.name",
    McpServer.ID,
    GenAI.TOOL_NAME,
    MCP.TOOL_STATUS,
    MCP.TOOL_DURATION_MS,
    Caller.TYPE,
    Caller.ID,
    Caller.TENANT,
    MCP.USER_ID,
    MCP.SESSION_ID,
    Gate.NAME,
    Gate.REASON,
    L7.VERDICT,
    L7.MODE,
    L7.RULE_KIND,
    L7.POLICY_ID,
}


def _refused(bus: Mock) -> list[ToolCallRefused]:
    return [c.args[0] for c in bus.publish.call_args_list if isinstance(c.args[0], ToolCallRefused)]


def _exported(event: ToolCallRefused) -> dict[str, Any]:
    """The attributes the production OTLP audit exporter writes for *event*."""
    exporter = OTLPAuditExporter()
    with patch.object(exporter, "_emit_log_record") as emit:
        OTLPAuditEventHandler(audit_exporter=exporter).handle(event)
    [call] = emit.call_args_list
    return dict(call.args[0])


def _assert_bounded(record: dict[str, Any], *outside_text: str) -> None:
    assert set(record) <= _ALLOWED, set(record) - _ALLOWED
    for text in (CANARY, *outside_text):
        assert not [v for v in record.values() if text in str(v)], text


class TestEveryGate:
    def test_every_gate_is_named_here(self) -> None:
        """A stage added to `_GATES` fails this until a case below refuses through it."""
        names = {gate.__name__.removeprefix("_gate_") for gate in _GATES}

        assert set(gate_cases._THROUGH_EXECUTE) | set(gate_cases._CANCELLATIONS) == names

    @pytest.mark.parametrize("gate", sorted(gate_cases._THROUGH_EXECUTE))
    def test_a_refusal_is_one_record_naming_the_gate_and_its_reason(self, ctx, gate: str) -> None:
        arrange, planted = gate_cases._THROUGH_EXECUTE[gate]
        if gate != "deferred_digest_pin":  # that one needs a catalogue not loaded yet
            _arrange_catalogue()
        with ExitStack() as stack:
            run_kwargs = arrange(ctx, stack)
            result = _run(**run_kwargs)

        assert result.success is False
        [event] = _refused(ctx.event_bus)
        assert (event.gate, event.correlation_id, event.tool_name) == (gate, "c-1", _TOOL)
        record = _exported(event)
        assert record[MCP.TOOL_STATUS] == "denied"
        assert record[Gate.NAME] == gate and record.get(Gate.REASON) == event.gate_reason
        assert record[Caller.TENANT] == _TENANT and record[Caller.TYPE] == "anonymous"
        _assert_bounded(record, result.error)
        assert (CANARY in result.error) is planted, "the canary went in, so its absence is a result"

    @pytest.mark.parametrize("gate", gate_cases._CANCELLATIONS)
    def test_a_cancellation_is_one_record(self, gate: str) -> None:
        call = CallSpec(index=0, call_id="c-1", mcp_server=_SERVER, tool=_TOOL, arguments={})
        cancelled = Event()
        cancelled.set()

        def refuse(error: str, error_type: str) -> CallResult:
            return CallResult(index=0, call_id="c-1", success=False, error=error, error_type=error_type, elapsed_ms=0)

        p = SimpleNamespace(
            call=call,
            ctx=Mock(),
            cancel_event=cancelled,
            refuse=refuse,
            gate_note=None,
            caller_tenant_id=_TENANT,
            global_timeout=60.0,
            batch_start_time=time.perf_counter(),
        )
        refusal = getattr(BatchExecutor, f"_gate_{gate}")(BatchExecutor(), p)
        _publish_gate_refusal(p, gate, refusal)  # type: ignore[arg-type]

        [event] = _refused(p.ctx.event_bus)
        assert (event.gate, event.gate_reason) == (gate, "cancelled")
        _assert_bounded(_exported(event), refusal.error)

    def test_a_gate_that_broke_is_no_refusal(self, ctx) -> None:
        _arrange_catalogue()
        ctx.get_mcp_server.return_value.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError(CANARY)

        assert _run().error_type == "McpServerStartError"
        assert _refused(ctx.event_bus) == []

    def test_a_budget_spent_after_the_gates_is_one_record(self, ctx) -> None:
        """The tenant budget also refuses once every gate has passed, when its slot is taken (#1445)."""
        _arrange_catalogue()

        def over_budget(self: BatchExecutor, p: Any) -> CallResult:
            return self._refuse_over_budget(p, Refusal(budget=_TENANT, reason="concurrency"))

        with patch.object(BatchExecutor, "_enforce_tenant_budget", over_budget):
            assert _run().error_type == "TenantQuotaExceeded"

        [event] = _refused(ctx.event_bus)
        assert (event.gate, event.gate_reason) == ("tenant_budget", "concurrency")

    def test_an_allowed_call_publishes_no_refusal(self, ctx) -> None:
        _arrange_catalogue()

        assert _run().success is True
        assert _refused(ctx.event_bus) == []


class TestAnL7Refusal:
    @pytest.mark.parametrize(
        ("rules", "verdict"),
        [(ToolRules(deny=(_TOOL,)), "deny"), (ToolRules(require_approval=(_TOOL,)), "require_approval")],
        ids=["deny", "require_approval"],
    )
    def test_is_one_record_with_the_bounded_verdict(self, ctx, rules, verdict: str) -> None:
        policy = l7_cases._policy(tools=rules)
        l7_cases._serve(ctx, policy)

        result = l7_cases._run()

        assert result.success is False
        [event] = _refused(ctx.event_bus)
        record = _exported(event)
        assert record[MCP.TOOL_STATUS] == "denied"
        assert (record[L7.VERDICT], record[L7.MODE], record[L7.RULE_KIND]) == (verdict, "enforce", "tool")
        assert record[L7.POLICY_ID] == policy.policy_id
        assert Gate.NAME not in record, "an L7 verdict is not a gate (ADR-029 s5)"
        _assert_bounded(record, result.error, "matched")

    def test_an_evaluator_failure_is_no_refusal(self, ctx, monkeypatch) -> None:
        """Fail-closed, and still the evaluator broke: `batch_call_failed`, and no refusal record."""
        from mcp_hangar.domain.policies import egress_l7

        monkeypatch.setattr(egress_l7, "scan_arguments", l7_cases._inspection_breaks)
        l7_cases._serve(ctx, l7_cases._policy(tools=ToolRules(allow=(_TOOL,))))

        assert l7_cases._run().error_type == "EgressPolicyDeniedError"
        assert _refused(ctx.event_bus) == []


class TestAToolInvokeDenial:
    def _authorize(self, authorize: Any, principal: Any) -> tuple[dict[int, CallResult], Mock]:
        app = Mock()
        app.auth_components.enabled = True
        app.auth_components.authz_middleware.authorize.side_effect = authorize
        calls = [{"mcp_server": _SERVER, "tool": _TOOL, "arguments": {}}]
        with patch("mcp_hangar.server.tools.batch.get_context", return_value=app):
            denied = _authorize_calls(calls, ["c-1"], principal, "b", identity=_identity(_TENANT))
        return denied, app.event_bus

    def test_a_missing_permission_is_one_record(self) -> None:
        principal = Mock(is_anonymous=Mock(return_value=False))
        refusal = AccessDeniedError(principal_id="p", action="invoke", resource=f"tool:{_TOOL}", reason=CANARY)

        denied, bus = self._authorize(refusal, principal)

        assert list(denied) == [0]
        [event] = _refused(bus)
        record = _exported(event)
        assert (record[Gate.NAME], record[Gate.REASON]) == ("authorization", "tool_invoke_denied")
        assert (record[MCP.TOOL_STATUS], record[Caller.TENANT]) == ("denied", _TENANT)
        _assert_bounded(record, denied[0].error)

    def test_an_anonymous_caller_is_one_record(self) -> None:
        denied, bus = self._authorize(None, None)

        assert list(denied) == [0]
        [event] = _refused(bus)
        assert (event.gate, event.gate_reason) == ("authorization", "unauthenticated")

    def test_an_authorizer_that_broke_is_no_refusal(self) -> None:
        principal = Mock(is_anonymous=Mock(return_value=False))

        denied, bus = self._authorize(RuntimeError(CANARY), principal)

        assert list(denied) == [0], "still fail-closed"
        assert _refused(bus) == []


def test_the_existing_refusal_events_are_not_audited_twice() -> None:
    """One refusal, one record: the refusal-specific events stay off the audit handler."""
    from mcp_hangar.domain.events import AuthorizationDenied, EgressPolicyEnforced, ToolApprovalDenied

    exporter = Mock()
    handler = OTLPAuditEventHandler(audit_exporter=exporter)
    handler.handle(ToolApprovalDenied(approval_id="a", mcp_server_id=_SERVER, tool_name=_TOOL, reason=CANARY))
    handler.handle(
        AuthorizationDenied(principal_id="p", action="invoke", resource_type="tool", resource_id=_TOOL, reason="x")
    )
    handler.handle(EgressPolicyEnforced(mcp_server_id=_SERVER, tool_name=_TOOL, action="deny", reasons=[CANARY]))

    assert exporter.export_tool_invocation.call_args_list == []
