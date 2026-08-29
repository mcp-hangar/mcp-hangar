"""An Enforce-mode egress refusal is at least as observable as an Audit one (#1128).

Audit mode -- the mode that by definition changes nothing -- recorded a domain
event, a warning and a metric. Enforce mode, refusing the call for real,
recorded a `logger.debug` line in the batch fault barrier carrying the generic
caller-facing message, with the reasons the policy computed dropped on the way
out. The enforcing verdict was the least auditable one in the product, and it is
the one an auditor, a SIEM export, or a second team reading a refusal asks
about: "which calls did this policy refuse yesterday" had no answer in anything
Hangar wrote.

`mcp_hangar_tool_call_errors_total` could not cover it either: it is fed from
`ToolInvocationFailed`, whose three emitters are all *past* the gate, so it is
structurally blind to a refusal rather than broken.
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from mcp_hangar import metrics as prometheus_metrics
from mcp_hangar.domain.events.enforcement import EgressPolicyEnforced, EgressPolicyViolationObserved
from mcp_hangar.domain.exceptions import EgressPolicyApprovalRequiredError, EgressPolicyDeniedError
from mcp_hangar.domain.model.mcp_server import McpServer
from mcp_hangar.domain.policies.egress_l7 import (
    ArgumentRules,
    evaluate,
    HeaderMatch,
    HeaderRules,
    L7Policy,
    PolicyMode,
    ToolRules,
)
from mcp_hangar.infrastructure.observability.metrics_event_handler import MetricsEventHandler
from mcp_hangar.metrics import get_metrics


class _Proceeded(Exception):
    """Raised from a stubbed ensure_ready: the call got past the L7 gate."""


def _server(policy: L7Policy) -> McpServer:
    server = McpServer(mcp_server_id="s", mode="subprocess", command=["echo"], l7_policy=policy)
    server.ensure_ready = Mock(side_effect=_Proceeded())  # type: ignore[method-assign]
    return server


def _enforced(server: McpServer) -> list[EgressPolicyEnforced]:
    return [e for e in server.collect_events() if isinstance(e, EgressPolicyEnforced)]


def _counter(action: str, rule_kind: str) -> float:
    return next(
        (
            sample.value
            for sample in prometheus_metrics.EGRESS_POLICY_ENFORCED_TOTAL.collect()
            if sample.labels.get("action") == action and sample.labels.get("rule_kind") == rule_kind
        ),
        0.0,
    )


class TestTheRefusalIsRecorded:
    def test_a_deny_records_an_event(self) -> None:
        policy = L7Policy(tools=ToolRules(deny=("refund",)))
        server = _server(policy)

        with pytest.raises(EgressPolicyDeniedError):
            server.invoke_tool("refund", {})

        events = _enforced(server)
        assert len(events) == 1
        assert events[0].action == "deny"
        assert events[0].tool_name == "refund"
        assert events[0].mcp_server_id == "s"

    def test_the_record_carries_the_reason_the_policy_computed(self) -> None:
        """The reason used to reach `.details` and stop there: the batch fault
        barrier logged `str(e)`, which is the generic caller-facing message."""
        server = _server(L7Policy(tools=ToolRules(deny=("refund",))))

        with pytest.raises(EgressPolicyDeniedError):
            server.invoke_tool("refund", {})

        assert any("deny rule" in reason for reason in _enforced(server)[0].reasons)

    def test_the_record_names_the_policy(self) -> None:
        policy = L7Policy(tools=ToolRules(deny=("refund",)))
        server = _server(policy)

        with pytest.raises(EgressPolicyDeniedError):
            server.invoke_tool("refund", {})

        assert _enforced(server)[0].policy_id == policy.policy_id

    def test_an_approval_refusal_is_recorded_too(self) -> None:
        """A requireApproval verdict nobody answered is a refusal, not a pause."""
        server = _server(L7Policy(tools=ToolRules(require_approval=("refund",))))

        with pytest.raises(EgressPolicyApprovalRequiredError):
            server.invoke_tool("refund", {})

        assert _enforced(server)[0].action == "require_approval"

    def test_a_permitted_call_records_nothing(self) -> None:
        server = _server(L7Policy(tools=ToolRules(allow=("*",))))

        with pytest.raises(_Proceeded):  # got past the gate
            server.invoke_tool("charge", {})

        assert _enforced(server) == []

    def test_audit_mode_still_records_only_the_observation(self) -> None:
        """The two events are siblings, not duplicates: Audit did not block, so
        nothing was enforced."""
        server = _server(L7Policy(tools=ToolRules(deny=("refund",)), mode=PolicyMode.AUDIT))

        with pytest.raises(_Proceeded):
            server.invoke_tool("refund", {})

        events = server.collect_events()
        assert any(isinstance(e, EgressPolicyViolationObserved) for e in events)
        assert not any(isinstance(e, EgressPolicyEnforced) for e in events)


class TestTheCounter:
    def test_the_counter_is_on_the_exposition(self) -> None:
        """A metric defined and never registered is the #1059 class."""
        assert "mcp_hangar_egress_policy_enforced" in get_metrics()

    def test_a_refusal_increments_it_with_the_rule_kind_that_matched(self) -> None:
        before = _counter("deny", "tool")

        MetricsEventHandler().handle(
            EgressPolicyEnforced(mcp_server_id="s", tool_name="refund", action="deny", rule_kind="tool")
        )

        assert _counter("deny", "tool") == before + 1

    def test_an_argument_violation_is_labelled_as_one(self) -> None:
        before = _counter("deny", "arguments")

        MetricsEventHandler().handle(
            EgressPolicyEnforced(mcp_server_id="s", tool_name="charge", action="deny", rule_kind="arguments")
        )

        assert _counter("deny", "arguments") == before + 1


class TestTheRuleKindTheVerdictRestsOn:
    def test_a_tool_rule(self) -> None:
        assert evaluate("refund", {}, L7Policy(tools=ToolRules(deny=("refund",)))).rule_kind == "tool"

    def test_a_header_selector(self) -> None:
        policy = L7Policy(
            tools=ToolRules(allow=("*",)),
            headers=HeaderRules(deny=(HeaderMatch("Mcp-Param-Region", ("eu-*",)),)),
        )
        headers = {"mcp-param-region": "eu-west-1", "mcp-protocol-version": "2026-07-28"}

        assert evaluate("charge", {}, policy, headers).rule_kind == "header"

    def test_an_argument_violation_overrides_the_ladder_that_allowed_it(self) -> None:
        """Deny always wins, so the verdict rests on the argument rule -- the
        counter must not report the tool rule that said allow."""
        policy = L7Policy(tools=ToolRules(allow=("*",)), arguments=ArgumentRules(secret_patterns=("aws-keys",)))

        decision = evaluate("charge", {"key": "AKIAIOSFODNN7EXAMPLE"}, policy)

        assert decision.rule_kind == "arguments"

    def test_the_event_carries_it_from_the_call_path(self) -> None:
        policy = L7Policy(tools=ToolRules(allow=("*",)), arguments=ArgumentRules(secret_patterns=("aws-keys",)))
        server = _server(policy)

        with pytest.raises(EgressPolicyDeniedError):
            server.invoke_tool("charge", {"key": "AKIAIOSFODNN7EXAMPLE"})

        assert _enforced(server)[0].rule_kind == "arguments"


class TestTheRecordSurvivesTheExceptionThatCarriesIt:
    def test_the_invoke_handler_publishes_the_refusal(self) -> None:
        """The event is recorded before the raise, and the handler publishes in a
        `finally` -- so a refusal reaches the bus even though the call ends in an
        exception. Without that ordering the record would exist and never leave
        the aggregate."""
        from unittest.mock import Mock as _Mock

        from mcp_hangar.application.commands.commands import InvokeToolCommand
        from mcp_hangar.application.commands.handlers import InvokeToolHandler

        server = _server(L7Policy(tools=ToolRules(deny=("refund",))))
        repository = _Mock()
        repository.get.return_value = server
        event_bus = _Mock()
        handler = InvokeToolHandler(repository=repository, event_bus=event_bus)

        with pytest.raises(EgressPolicyDeniedError):
            handler.handle(InvokeToolCommand(mcp_server_id="s", tool_name="refund", arguments={}))

        published = event_bus.publish_aggregate_events.call_args[0][2]
        assert any(isinstance(e, EgressPolicyEnforced) for e in published)


class TestTheFaultBarrierIsLoudForARefusal:
    """A deliberate refusal and an upstream blowing up were logged the same way:
    `logger.debug`, which a default deployment does not emit."""

    @staticmethod
    def _calls(monkeypatch):
        from mcp_hangar.server.tools.batch import executor as executor_module

        recorded: list[tuple[str, str, dict]] = []

        class _Recorder:
            def __getattr__(self, level):
                def _log(event, **fields):
                    recorded.append((level, event, fields))

                return _log

        monkeypatch.setattr(executor_module, "logger", _Recorder())
        return recorded

    def test_a_refusal_logs_at_warning_with_its_reason(self, monkeypatch) -> None:
        from mcp_hangar.server.tools.batch.executor import _log_call_failure

        recorded = self._calls(monkeypatch)
        error = EgressPolicyDeniedError("s", "refund", "tool 'refund' matched a deny rule", policy_id="sha256:abc")
        call = type("_Call", (), {"call_id": "c1", "mcp_server": "s", "tool": "refund"})()

        _log_call_failure(call, error, "EgressPolicyDeniedError", 1.0)

        level, event, fields = recorded[0]
        assert level == "warning"
        assert event == "batch_call_refused"
        assert fields["reason"] == "tool 'refund' matched a deny rule"
        assert fields["policy_id"] == "sha256:abc"

    def test_an_upstream_failure_stays_at_debug(self, monkeypatch) -> None:
        """A batch of failing calls must not become a log flood; the caller
        already gets the error in its CallResult."""
        from mcp_hangar.server.tools.batch.executor import _log_call_failure

        recorded = self._calls(monkeypatch)
        call = type("_Call", (), {"call_id": "c1", "mcp_server": "s", "tool": "read"})()

        _log_call_failure(call, TimeoutError("upstream gone"), "TimeoutError", 1.0)

        assert recorded[0][0] == "debug"
        assert recorded[0][1] == "batch_call_failed"
