"""The L7 verdict the aggregate applied is on the call's span, and its refusal line (#1295, ADR-029 s5).

Before this, an L7 refusal showed on a trace only as `error.type` and
`hangar.call.outcome=deny`; an allow and an Audit observation showed nothing,
and the policy, mode and rule the verdict rested on never reached a span. The
refusal's log line carried the policy's reasons joined into free text.

Driven through `BatchExecutor.execute`, a real `CommandBus` and a real
`McpServer` with its upstream stubbed, under `_TextFreeTracer` on a real SDK
provider: the spans are the ones production writes, including the inner
`invoke_with_retry`, `command.send`, `dispatch` and `handler` spans an L7
refusal escapes.

Naming: neutral placeholders only (server_a, read_item).
"""

from __future__ import annotations

import ast
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, patch

import pytest
import structlog

from mcp_hangar.application.commands import InvokeToolCommand
from mcp_hangar.application.commands.handlers import InvokeToolHandler
from mcp_hangar.approvals.models import ApprovalResult
from mcp_hangar.context import identity_context_var
from mcp_hangar.domain.contracts.l7_verdict_observer import (
    L7Verdict,
    L7VerdictKind,
    NullL7VerdictObserver,
    get_default_l7_verdict_observer,
    set_default_l7_verdict_observer,
)
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies import egress_l7
from mcp_hangar.domain.policies.egress_l7 import ArgumentRules, L7Policy, PolicyMode, ToolAction, ToolRules
from mcp_hangar.domain.value_objects import McpServerState
from mcp_hangar.infrastructure.command_bus import CommandBus
from mcp_hangar.infrastructure.event_bus import EventBus
from mcp_hangar.infrastructure.observability.l7_verdicts import ContextL7VerdictObserver, bounded_l7_decision
from mcp_hangar.observability.conventions import L7, Gate
from mcp_hangar.server.tools.batch import BatchExecutor, CallSpec
from tests.unit import test_batch_gate_precedence as precedence
from tests.unit.test_batch_gate_precedence import _SERVER, _TENANT, _TOOL, _arrange_catalogue, _identity

pytestmark = pytest.mark.otel_sdk

ctx = precedence.ctx
_reset_singletons = precedence._reset_singletons

#: A value shaped like the `aws-keys` secret-pattern group, and nothing else.
_AWS_SHAPED = "AKIA" + "Q" * 16
_INNER = (
    "invoke_with_retry",
    "command.send.InvokeToolCommand",
    "dispatch.InvokeToolCommand",
    "handler.InvokeToolCommand",
)


@pytest.fixture(autouse=True)
def observer() -> Iterator[None]:
    """The adapter bootstrap installs (`init_l7_verdict_observer`), restored afterwards."""
    previous = get_default_l7_verdict_observer()
    set_default_l7_verdict_observer(ContextL7VerdictObserver())
    yield
    set_default_l7_verdict_observer(previous)


@pytest.fixture
def spans() -> Iterator[Any]:
    """`_TextFreeTracer` over an in-memory SDK provider, where the executor and the command bus read it."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = _TextFreeTracer(provider.get_tracer("test"))
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=tracer),
        patch("mcp_hangar.infrastructure.command_bus.get_tracer", return_value=tracer),
    ):
        yield memory


def _serve(ctx: Any, policy: L7Policy, *, gate: Any = None) -> McpServer:
    """A ready server behind a real command bus, its upstream answering every call."""
    server = McpServer(mcp_server_id=_SERVER, mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock()  # type: ignore[method-assign]
    server._client = MagicMock(call=Mock(return_value={"result": {"content": []}}))
    server._state = McpServerState.READY
    server._tools.update_from_list([{"name": _TOOL}])
    repository = Mock(get=Mock(return_value=server))
    bus = CommandBus()
    bus.register(InvokeToolCommand, InvokeToolHandler(repository, EventBus()))
    ctx.command_bus = bus
    ctx.repository = repository
    ctx.approval_gate = gate
    _arrange_catalogue()
    return server


def _run(arguments: dict[str, Any] | None = None) -> Any:
    token = identity_context_var.set(_identity(_TENANT))
    try:
        call = CallSpec(
            index=0, call_id="c-1", mcp_server=_SERVER, tool=_TOOL, arguments=arguments or {}, max_retries=2
        )
        batch = BatchExecutor().execute(
            batch_id="b", calls=[call], max_concurrency=1, global_timeout=30.0, fail_fast=False
        )
    finally:
        identity_context_var.reset(token)
    return batch.results[0]


def _span(spans: Any, name: str = f"batch.call.{_TOOL}") -> Any:
    (span,) = [s for s in spans.get_finished_spans() if s.name == name]
    return span


def _l7(spans: Any) -> dict[str, Any]:
    return {k: v for k, v in _span(spans).attributes.items() if k.startswith("hangar.l7.")}


def _lines(captured: list[dict[str, Any]], event: str) -> list[dict[str, Any]]:
    return [line for line in captured if line["event"] == event]


def _policy(**kwargs: Any) -> L7Policy:
    return L7Policy(**kwargs)


class TestAnAllowedCall:
    def test_records_allow_with_the_mode_the_rule_and_the_policy(self, ctx, spans) -> None:
        policy = _policy(tools=ToolRules(allow=(_TOOL,)))
        _serve(ctx, policy)

        assert _run().success is True

        assert _l7(spans) == {
            L7.VERDICT: "allow",
            L7.MODE: "enforce",
            L7.RULE_KIND: "tool",
            L7.POLICY_ID: policy.policy_id,
        }
        assert _span(spans).attributes[Gate.CALL_OUTCOME] == Gate.ALLOW

    def test_a_server_without_a_policy_records_no_verdict(self, ctx, spans) -> None:
        server = _serve(ctx, _policy())
        server.set_l7_policy(None)

        assert _run().success is True
        assert _l7(spans) == {}


class TestAnAuditObservation:
    @pytest.mark.parametrize(
        "rules", [ToolRules(deny=(_TOOL,)), ToolRules(require_approval=(_TOOL,))], ids=["deny", "require_approval"]
    )
    def test_records_audit_observed_and_nothing_else_changes(self, ctx, spans, rules) -> None:
        _serve(ctx, _policy(tools=rules, mode=PolicyMode.AUDIT))

        with structlog.testing.capture_logs() as captured:
            assert _run().success is True

        assert _l7(spans)[L7.VERDICT] == "audit_observed"
        assert _l7(spans)[L7.MODE] == "audit"
        attributes = _span(spans).attributes
        assert attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
        assert Gate.REFUSAL_GATE not in attributes and Gate.REFUSAL_REASON not in attributes
        assert _span(spans).status.status_code.name == "UNSET"
        assert not _lines(captured, "batch_call_refused")


_DENIALS = {
    "tool_rule": (_policy(tools=ToolRules(deny=(_TOOL,))), {}, "tool"),
    "default_action": (_policy(default_action=ToolAction.DENY), {}, "tool"),
    "secret_pattern": (
        _policy(tools=ToolRules(allow=(_TOOL,)), arguments=ArgumentRules(secret_patterns=("aws-keys",))),
        {"note": _AWS_SHAPED},
        "argument",
    ),
    "size_limit": (
        _policy(tools=ToolRules(allow=(_TOOL,)), arguments=ArgumentRules(max_payload_bytes=8)),
        {"note": "x" * 64},
        "argument",
    ),
}


class TestADenial:
    @pytest.mark.parametrize("case", sorted(_DENIALS))
    def test_records_deny_and_the_rule_it_rests_on(self, ctx, spans, case) -> None:
        policy, arguments, rule_kind = _DENIALS[case]
        _serve(ctx, policy)

        assert _run(arguments).error_type == "EgressPolicyDeniedError"

        assert _l7(spans) == {
            L7.VERDICT: "deny",
            L7.MODE: "enforce",
            L7.RULE_KIND: rule_kind,
            L7.POLICY_ID: policy.policy_id,
        }
        assert _span(spans).attributes[Gate.CALL_OUTCOME] == Gate.DENY

    def test_is_not_a_gate_refusal_and_leaves_every_span_unset(self, ctx, spans) -> None:
        _serve(ctx, _DENIALS["tool_rule"][0])
        _run()

        attributes = _span(spans).attributes
        assert Gate.REFUSAL_GATE not in attributes and Gate.REFUSAL_REASON not in attributes
        for name in (f"batch.call.{_TOOL}", *_INNER):
            assert _span(spans, name).status.status_code.name == "UNSET", name

    @pytest.mark.parametrize("case", sorted(_DENIALS))
    def test_logs_the_bounded_verdict_not_the_policy_reasons(self, ctx, spans, case) -> None:
        policy, arguments, rule_kind = _DENIALS[case]
        _serve(ctx, policy)

        with structlog.testing.capture_logs() as captured:
            _run(arguments)

        [line] = _lines(captured, "batch_call_refused")
        assert (line["l7_verdict"], line["l7_mode"], line["l7_rule_kind"]) == ("deny", "enforce", rule_kind)
        assert line["policy_id"] == policy.policy_id
        assert "reason" not in line
        for text in ("matched", "exceeds", "secret matching", "default action", _AWS_SHAPED):
            assert text not in repr(line), text


class TestARoutingToApproval:
    def test_nobody_to_ask_records_require_approval(self, ctx, spans) -> None:
        _serve(ctx, _policy(tools=ToolRules(require_approval=(_TOOL,))))

        assert _run().error_type == "EgressPolicyApprovalRequiredError"

        assert _l7(spans)[L7.VERDICT] == "require_approval"
        assert _span(spans).attributes[Gate.CALL_OUTCOME] == Gate.DENY
        assert Gate.REFUSAL_GATE not in _span(spans).attributes
        for name in (f"batch.call.{_TOOL}", *_INNER):
            assert _span(spans, name).status.status_code.name == "UNSET", name

    def test_a_granted_approval_records_approval_honored_beside_the_gate_event(self, ctx, spans) -> None:
        class _Grants:
            async def check(self, **_kwargs: Any) -> ApprovalResult:
                return ApprovalResult.granted("appr-1")

        _serve(ctx, _policy(tools=ToolRules(require_approval=(_TOOL,))), gate=_Grants())

        assert _run().success is True

        span = _span(spans)
        assert _l7(spans)[L7.VERDICT] == "approval_honored"
        approvals = [e for e in span.events if e.attributes.get(Gate.NAME) == "approval"]
        assert approvals and approvals[0].attributes[Gate.OUTCOME] == Gate.ALLOW
        assert not [v for k, v in span.attributes.items() if k.startswith("hangar.l7.") and v == "appr-1"]


def _inspection_breaks(*_args: Any, **_kwargs: Any) -> list[str]:
    raise RecursionError("nested too deep")


class TestAnEvaluatorFailure:
    """ADR-029 s5: the verdict is a denial, and the evaluator still broke."""

    def test_in_enforce_the_call_span_is_error_with_outcome_error(self, ctx, spans, monkeypatch) -> None:
        monkeypatch.setattr(egress_l7, "scan_arguments", _inspection_breaks)
        _serve(ctx, _policy(tools=ToolRules(allow=(_TOOL,))))

        with structlog.testing.capture_logs() as captured:
            assert _run().error_type == "EgressPolicyDeniedError"

        span = _span(spans)
        assert span.status.status_code.name == "ERROR"
        assert span.attributes[Gate.CALL_OUTCOME] == Gate.ERROR
        assert (_l7(spans)[L7.VERDICT], _l7(spans)[L7.RULE_KIND]) == ("deny", "argument")
        # A recorded deviation: the refusal exception still leaves the inner spans UNSET.
        for name in _INNER:
            assert _span(spans, name).status.status_code.name == "UNSET", name
        assert not _lines(captured, "batch_call_refused")
        [line] = _lines(captured, "batch_call_failed")
        assert (line["log_level"], line["l7_inspection_failed"]) == ("warning", True)

    def test_in_audit_the_flag_goes_to_the_log_only(self, ctx, spans, monkeypatch) -> None:
        monkeypatch.setattr(egress_l7, "scan_arguments", _inspection_breaks)
        _serve(ctx, _policy(tools=ToolRules(allow=(_TOOL,)), mode=PolicyMode.AUDIT))

        with structlog.testing.capture_logs() as captured:
            assert _run().success is True

        span = _span(spans)
        assert span.status.status_code.name == "UNSET"
        assert span.attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
        assert set(_l7(spans)) == {L7.VERDICT, L7.MODE, L7.RULE_KIND, L7.POLICY_ID}, "no new span key"
        [line] = _lines(captured, "egress_policy_violation_observed")
        assert line["inspection_failed"] is True


class TestTheObservationChangesNothing:
    def _evaluations(self, ctx, monkeypatch, policy: L7Policy) -> int:
        calls = []
        real = egress_l7.evaluate

        def counting(*args: Any, **kwargs: Any) -> Any:
            calls.append(threading.get_ident())
            return real(*args, **kwargs)

        monkeypatch.setattr(egress_l7, "evaluate", counting)
        _serve(ctx, policy)
        _run()
        return len(calls)

    @pytest.mark.parametrize("case", ["tool_rule", "secret_pattern"])
    def test_no_extra_evaluation_per_dispatch(self, ctx, spans, monkeypatch, case) -> None:
        policy = _DENIALS[case][0]
        observed = self._evaluations(ctx, monkeypatch, policy)
        set_default_l7_verdict_observer(NullL7VerdictObserver())
        unobserved = self._evaluations(ctx, monkeypatch, policy)

        assert observed == unobserved

    def test_the_null_observer_leaves_the_call_as_it_was(self, ctx, spans) -> None:
        set_default_l7_verdict_observer(NullL7VerdictObserver())
        _serve(ctx, _DENIALS["tool_rule"][0])

        result = _run()

        assert (result.success, result.error_type) == (False, "EgressPolicyDeniedError")
        assert _l7(spans) == {}
        assert _span(spans).attributes[Gate.CALL_OUTCOME] == Gate.DENY

    def test_a_broken_observer_does_not_change_the_verdict(self, ctx, spans) -> None:
        class _Broken(NullL7VerdictObserver):
            def observe(self, verdict: L7Verdict) -> None:
                raise RuntimeError("observer broke")

        set_default_l7_verdict_observer(_Broken())
        _serve(ctx, _DENIALS["tool_rule"][0])

        assert _run().error_type == "EgressPolicyDeniedError"


class TestTheBoundedValues:
    def _verdict(self, **kwargs: Any) -> L7Verdict:
        fields = {"verdict": L7VerdictKind.DENY, "mode": "Enforce", "rule_kind": "tool", "policy_id": None}
        return L7Verdict(**{**fields, **kwargs})

    def test_arguments_is_exported_as_argument(self) -> None:
        decision = bounded_l7_decision(self._verdict(rule_kind="arguments"))
        assert decision is not None and decision.rule_kind == "argument"

    @pytest.mark.parametrize("policy_id", ["sha256:zz", "md5:abcdef", "sha256:" + "a" * 65, "a free sentence"])
    def test_a_policy_id_of_another_shape_is_omitted(self, policy_id: str) -> None:
        decision = bounded_l7_decision(self._verdict(policy_id=policy_id))
        assert decision is not None and decision.policy_id is None

    def test_a_real_policy_id_passes(self) -> None:
        policy_id = _policy().policy_id
        decision = bounded_l7_decision(self._verdict(policy_id=policy_id))
        assert decision is not None and decision.policy_id == policy_id

    def test_an_unknown_mode_or_rule_kind_is_omitted(self) -> None:
        decision = bounded_l7_decision(self._verdict(mode="Sometimes", rule_kind="body"))
        assert decision is not None and (decision.mode, decision.rule_kind) == (None, None)


def test_bootstrap_installs_the_adapter() -> None:
    from mcp_hangar.server.bootstrap.observability import init_l7_verdict_observer

    set_default_l7_verdict_observer(NullL7VerdictObserver())
    init_l7_verdict_observer()

    assert isinstance(get_default_l7_verdict_observer(), ContextL7VerdictObserver)


def test_the_domain_port_imports_no_opentelemetry() -> None:
    import mcp_hangar.domain as domain

    for source in Path(domain.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else []
            if isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            assert not [n for n in names if n.startswith("opentelemetry")], source
