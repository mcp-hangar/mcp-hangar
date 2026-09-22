"""Each batch gate says what it decided, and why, on the call's span (#1285, ADR-029 s5).

`_GATES` runs thirteen stages and only three of them opened a span, so a refused
call's trace could not say which gate refused it, and a call that passed could
not say which gates had applied. A gate returns ``None`` both when it let the
call through and when it did not apply at all, so a wrapper around the loop
cannot tell those apart: here the gates that skip or defer say so themselves,
and these tests pin the difference -- `digest_pin` with no pin is ``skip``, with
a matching pin ``allow`` and the pinned digest, with no catalogue yet
``deferred``, and the deferred check records its own later outcome.

Driven through `BatchExecutor.execute` with the precedence tests' arrangements
and a real SDK provider, so the events are the ones the executor really writes
on `batch.call.<tool>`, in the order its gates really run.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import ExitStack
from typing import Any
from unittest.mock import patch

import pytest

from mcp_hangar.application.read_models.tool_projection import (
    get_tool_projection_registry,
    reset_tool_projection_registry,
)
from mcp_hangar.domain.services.digest_computation import compute_tool_digest
from mcp_hangar.domain.value_objects import ToolDigest
from mcp_hangar.observability.conventions import Gate
from mcp_hangar.server.tools.batch import BatchExecutor
from mcp_hangar.server.tools.batch.executor import _GATES
from mcp_hangar.server.tools.batch.models import CallResult
from mcp_hangar.server.tools.batch.tenant_admission import (
    Reservation,
    TenantLimits,
    configure_tenant_limits,
    get_tenant_admission,
    reset_tenant_admission,
)
from tests.unit import test_batch_gate_precedence as precedence
from tests.unit.test_batch_gate_precedence import (
    _SERVER,
    _STALE_DIGEST,
    _TENANT,
    _TOOL,
    _arrange_catalogue,
    _arrange_circuit_open,
    _arrange_deferred_pin,
    _arrange_server_missing,
    _arrange_stale_pin,
    _arrange_tool_access_denied,
    _arrange_withdrawn,
    _refused_by_a_validator,
    _run,
)

pytestmark = pytest.mark.otel_sdk

# The precedence tests' fixtures: a healthy ready server, and fresh singletons around each test.
ctx = precedence.ctx
_reset_singletons = precedence._reset_singletons

_NAMES = [gate.__name__.removeprefix("_gate_") for gate in _GATES]


@pytest.fixture
def exporter() -> Iterator[Any]:
    """A real TracerProvider whose spans land in memory, patched into the executor."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    with patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=provider.get_tracer("test")):
        yield memory


def _call_span(exporter: Any) -> Any:
    (span,) = [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{_TOOL}"]
    return span


def _decisions(exporter: Any) -> list[dict[str, Any]]:
    return [dict(e.attributes) for e in _call_span(exporter).events if e.name == Gate.DECISION_EVENT]


def _by_name(exporter: Any) -> dict[str, tuple[Any, ...]]:
    return {d[Gate.NAME]: (d[Gate.OUTCOME], d.get(Gate.REASON), d.get(Gate.REVISION)) for d in _decisions(exporter)}


def _real_digest() -> str:
    projection = get_tool_projection_registry().resolve(_SERVER, _TOOL, _TENANT)
    return compute_tool_digest(projection.schema).sha256


def _pin(sha256: str) -> None:
    get_tool_projection_registry().set_config_pin(_SERVER, _TOOL, _TENANT, ToolDigest(tool_name=_TOOL, sha256=sha256))


class TestACallThatPasses:
    def test_every_gate_records_one_decision_in_the_order_it_ran(self, ctx, exporter):
        _arrange_catalogue()

        assert _run().success is True
        assert [d[Gate.NAME] for d in _decisions(exporter)] == _NAMES

    def test_a_gate_that_does_not_apply_says_skip_not_allow(self, ctx, exporter):
        """The None-return ambiguity: each of these returned None, and none of them allowed anything."""
        _arrange_catalogue()
        _run()

        decisions = _by_name(exporter)
        assert decisions["digest_pin"] == (Gate.SKIP, "no_pin", None)
        assert decisions["cold_start"] == (Gate.SKIP, "not_cold", None)
        assert decisions["deferred_digest_pin"] == (Gate.SKIP, "not_deferred", None)
        assert decisions["approval"] == (Gate.SKIP, "not_required", None)
        for name in ("cancelled_before_execution", "global_timeout", "resolve_target", "tool_access"):
            assert decisions[name] == (Gate.ALLOW, None, None), name

    def test_the_call_is_allowed_and_names_no_refusal(self, ctx, exporter):
        _arrange_catalogue()
        _run()

        attributes = _call_span(exporter).attributes
        assert attributes[Gate.CALL_OUTCOME] == Gate.ALLOW
        assert Gate.REFUSAL_GATE not in attributes
        assert Gate.REFUSAL_REASON not in attributes

    def test_a_matching_pin_is_allowed_with_the_digest_it_was_checked_against(self, ctx, exporter):
        _arrange_catalogue()
        digest = _real_digest()
        _pin(digest)

        assert _run().success is True
        assert _by_name(exporter)["digest_pin"] == (Gate.ALLOW, None, digest)

    def test_no_reason_is_ever_empty(self, ctx, exporter):
        _arrange_catalogue()
        _run()

        assert all(d.get(Gate.REASON, "x") and d.get(Gate.REVISION, "x") for d in _decisions(exporter))


def _arrange(ctx, refusal: str, stack: ExitStack) -> None:
    _arrange_catalogue()
    if refusal == "resolve_target":
        _arrange_server_missing(ctx)
    elif refusal == "tool_access":
        _arrange_tool_access_denied()
    elif refusal == "withdrawal":
        _arrange_withdrawn()
    elif refusal == "digest_pin":
        _arrange_stale_pin()
    elif refusal == "circuit_breaker":
        _arrange_circuit_open(ctx)
    elif refusal == "validators":
        stack.enter_context(_refused_by_a_validator())


_REFUSALS = {
    "resolve_target": "server_not_found",
    "tool_access": "tool_not_in_access_policy",
    "withdrawal": "tool_withdrawn",
    "digest_pin": "digest_mismatch",
    "circuit_breaker": "circuit_open",
    "validators": "validator_denied",
}


class TestTheFirstRefusingGate:
    @pytest.mark.parametrize("gate", sorted(_REFUSALS))
    def test_it_records_deny_with_its_reason_and_nothing_runs_after_it(self, ctx, exporter, gate):
        with ExitStack() as stack:
            _arrange(ctx, gate, stack)
            assert _run().success is False

        decisions = _decisions(exporter)
        assert [d[Gate.NAME] for d in decisions] == _NAMES[: _NAMES.index(gate) + 1]
        last = decisions[-1]
        assert (last[Gate.OUTCOME], last[Gate.REASON]) == (Gate.DENY, _REFUSALS[gate])
        assert all(d[Gate.OUTCOME] != Gate.DENY for d in decisions[:-1])

        attributes = _call_span(exporter).attributes
        assert attributes[Gate.REFUSAL_GATE] == gate
        assert attributes[Gate.REFUSAL_REASON] == _REFUSALS[gate]
        assert attributes[Gate.CALL_OUTCOME] == Gate.DENY

        names = {s.name for s in exporter.get_finished_spans()}
        assert not names & {"mcp_server.cold_start", "invoke_with_retry", "command.send.InvokeToolCommand"}
        ctx.command_bus.send.assert_not_called()

    def test_a_stale_pin_names_the_digest_it_refused(self, ctx, exporter):
        _arrange_catalogue()
        _arrange_stale_pin()
        _run()

        assert _by_name(exporter)["digest_pin"] == (Gate.DENY, "digest_mismatch", _STALE_DIGEST)

    def test_a_spent_batch_timeout_is_the_second_gate(self, ctx, exporter):
        _arrange_catalogue()
        _run(global_timeout=-1.0)

        assert [(d[Gate.NAME], d[Gate.OUTCOME]) for d in _decisions(exporter)] == [
            ("cancelled_before_execution", Gate.ALLOW),
            ("global_timeout", Gate.DENY),
        ]
        assert _call_span(exporter).attributes[Gate.REFUSAL_REASON] == "batch_timeout"

    @pytest.mark.parametrize("reason", ["no_budget", "rate"])
    def test_a_tenant_budget_names_which_budget(self, ctx, exporter, reason):
        """The reason is the admission's own bounded code, not the refusal's single error_type."""
        _arrange_catalogue()
        if reason == "no_budget":
            configure_tenant_limits({"tenant:other": TenantLimits(max_concurrency=5, rps=1, burst=1)})
        else:
            configure_tenant_limits({_TENANT: TenantLimits(max_concurrency=5, rps=1e-9, burst=1)})
            assert isinstance(get_tenant_admission().reserve(_TENANT), Reservation)
        try:
            assert _run().error_type == "TenantQuotaExceeded"
        finally:
            reset_tenant_admission()

        assert _by_name(exporter)["tenant_budget"] == (Gate.DENY, reason, None)
        assert _call_span(exporter).attributes[Gate.REFUSAL_GATE] == "tenant_budget"


class TestADeferredPin:
    """A pin with no catalogue yet is `deferred`, and the check after the cold start records its own outcome."""

    def _start_populates(self, ctx) -> None:
        def start(_command: Any) -> dict[str, bool]:
            _arrange_catalogue()
            return {"ok": True}

        ctx.command_bus.send.side_effect = start

    def test_deferred_then_allowed(self, ctx, exporter):
        _arrange_catalogue()
        digest = _real_digest()
        _reset_catalogue()
        _pin(digest)
        ctx.get_mcp_server.return_value.state.value = "cold"
        self._start_populates(ctx)

        result = _run()

        assert result.success is True, result.error
        decisions = _by_name(exporter)
        assert decisions["digest_pin"] == (Gate.DEFERRED, "catalogue_not_loaded", None)
        assert decisions["cold_start"] == (Gate.ALLOW, None, None)
        assert decisions["deferred_digest_pin"] == (Gate.ALLOW, None, digest)

    def test_deferred_then_denied_on_the_same_span(self, ctx, exporter):
        _arrange_stale_pin()
        ctx.get_mcp_server.return_value.state.value = "cold"
        self._start_populates(ctx)

        assert _run().error_type == "ToolDigestMismatchError"
        decisions = _decisions(exporter)
        pins = [(d[Gate.NAME], d[Gate.OUTCOME]) for d in decisions if "digest_pin" in d[Gate.NAME]]
        assert pins == [("digest_pin", Gate.DEFERRED), ("deferred_digest_pin", Gate.DENY)]
        assert _by_name(exporter)["deferred_digest_pin"] == (Gate.DENY, "digest_mismatch", _STALE_DIGEST)
        assert _call_span(exporter).attributes[Gate.REFUSAL_GATE] == "deferred_digest_pin"

    def test_deferred_then_unverifiable(self, ctx, exporter):
        """The cold start never produced the tool: the fail-closed refusal says it could not verify."""
        _arrange_deferred_pin(ctx)

        assert _run().error_type == "ToolDigestMismatchError"
        assert _by_name(exporter)["deferred_digest_pin"] == (Gate.DENY, "digest_unverifiable", None)


def _reset_catalogue() -> None:
    reset_tool_projection_registry()


class TestAFailureIsNotARefusal:
    def test_a_failed_cold_start_is_an_error_and_ends_the_call(self, ctx, exporter):
        _arrange_catalogue()
        ctx.get_mcp_server.return_value.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError("boom")

        assert _run().error_type == "McpServerStartError"
        assert _by_name(exporter)["cold_start"] == (Gate.ERROR, "start_failed", None)
        attributes = _call_span(exporter).attributes
        assert attributes[Gate.CALL_OUTCOME] == Gate.ERROR
        assert attributes[Gate.REFUSAL_GATE] == "cold_start"

    def test_an_upstream_failure_after_every_gate_passed_is_an_error_with_no_refusal(self, ctx, exporter):
        _arrange_catalogue()
        failed = CallResult(index=0, call_id="c-1", success=False, error="x", error_type="RuntimeError", elapsed_ms=0)
        with patch.object(BatchExecutor, "_invoke_with_retry", return_value=failed):
            assert _run().success is False

        attributes = _call_span(exporter).attributes
        assert attributes[Gate.CALL_OUTCOME] == Gate.ERROR
        assert Gate.REFUSAL_GATE not in attributes
        assert all(d[Gate.OUTCOME] != Gate.DENY for d in _decisions(exporter))


class TestTelemetryNeverDecides:
    @pytest.mark.parametrize("refusal", [None, "tool_access", "digest_pin"])
    def test_a_failing_recorder_changes_no_verdict(self, ctx, exporter, refusal):
        with ExitStack() as stack:
            _arrange(ctx, refusal or "", stack)
            expected = _run()
        _reset_catalogue()

        with ExitStack() as stack:
            _arrange(ctx, refusal or "", stack)
            stack.enter_context(
                patch("mcp_hangar.server.tools.batch.executor.record_gate_decision", side_effect=RuntimeError)
            )
            stack.enter_context(
                patch("mcp_hangar.server.tools.batch.executor.record_call_outcome", side_effect=RuntimeError)
            )
            actual = _run()

        assert (actual.success, actual.error_type) == (expected.success, expected.error_type)

    def test_a_span_that_refuses_events_does_not_raise(self):
        from opentelemetry.sdk.trace import TracerProvider

        from mcp_hangar.observability.tracing import record_call_outcome, record_gate_decision

        with TracerProvider().get_tracer("t").start_as_current_span("s") as span:
            with (
                patch.object(span, "add_event", side_effect=RuntimeError),
                patch.object(span, "set_attribute", side_effect=RuntimeError),
            ):
                record_gate_decision("tool_access", Gate.DENY, "tool_not_in_access_policy", refused=True)
                record_call_outcome(Gate.DENY)

    def test_with_no_span_nothing_is_written_and_nothing_raises(self):
        from mcp_hangar.observability.tracing import record_call_outcome, record_gate_decision

        record_gate_decision("tool_access", Gate.ALLOW)
        record_call_outcome(Gate.ALLOW)

    def test_a_malformed_revision_is_omitted_not_replaced(self):
        from opentelemetry.sdk.trace import TracerProvider

        from mcp_hangar.observability.tracing import record_gate_decision

        with TracerProvider().get_tracer("t").start_as_current_span("s") as span:
            record_gate_decision("digest_pin", Gate.ALLOW, revision="not a digest")
        (event,) = span.events
        assert Gate.REVISION not in event.attributes
