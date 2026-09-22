"""A refusal is not a failure, and the span status has to say so (ADR-029 s5).

`_TextFreeTracer` marked every span an exception escaped as ERROR, and the batch
executor marked every unsuccessful call the same way. So an egress denial, a
call routed to approval and a spent rate-limit budget -- each of them the
gateway answering the question it exists to answer -- produced error traces, and
`hangar.gate.outcome=deny` from #1285 sat on a span whose status said failure.

What stays: the bounded `error.type`. It names the refusal class, and until the
L7 vocabulary lands (#1295) it is the only thing on the span that does.
"""

from __future__ import annotations

import inspect
from contextlib import ExitStack
from dataclasses import dataclass
from typing import Any

import pytest

from mcp_hangar.domain import exceptions as domain_exceptions
from mcp_hangar.domain.exceptions import (
    EgressPolicyApprovalRequiredError,
    EgressPolicyDeniedError,
    RateLimitExceeded,
    ToolTimeoutError,
)
from mcp_hangar.errors import ExpectedRefusal
from mcp_hangar.observability.conventions import Dispatch, Gate
from tests.unit import test_batch_gate_decisions as gate_decisions
from tests.unit.test_batch_gate_precedence import _run

# The #1285 harness's fixtures, by assignment as that module takes the
# precedence tests': a ready server with fresh singletons, and an in-memory
# exporter patched into the executor.
ctx = gate_decisions.ctx
executor_exporter = gate_decisions.exporter
#: Autouse where it is defined, so it has to be re-exported here too: without it
#: one test's arrangement survives into the next.
_reset_singletons = gate_decisions._reset_singletons

pytestmark = pytest.mark.otel_sdk

ERROR_TYPE = "error.type"


@pytest.fixture
def sdk(monkeypatch):
    """An in-memory exporter behind the wrapper `get_tracer` returns in production.

    The wrapper is what this change edits, so the tests exercise `_TextFreeTracer`
    itself rather than a bare SDK tracer, and patch it in where the code under
    test reads its tracer -- as `test_a_refused_command_says_what_was_refused`
    does. Swapping the global provider instead would leave the proxy provider
    pointing at itself.
    """
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = _TextFreeTracer(provider.get_tracer("test"))

    monkeypatch.setattr("mcp_hangar.infrastructure.command_bus.get_tracer", lambda *_a, **_k: tracer)
    memory.tracer = tracer
    return memory


def _refusal() -> EgressPolicyDeniedError:
    return EgressPolicyDeniedError(mcp_server_id="s", tool_name="t", reason="denied_by_rule", policy_id="p1")


def _status_and_attributes(exporter) -> tuple[str, dict[str, Any], list[str]]:
    [span] = exporter.get_finished_spans()
    return span.status.status_code.name, dict(span.attributes or {}), [event.name for event in span.events]


class TestAnEscapingRefusal:
    """`_TextFreeTracer`: the status depends on what escaped, not that something did."""

    def _run(self, sdk, error: Exception) -> None:
        with pytest.raises(type(error)):
            with sdk.tracer.start_as_current_span("hangar.span"):
                raise error

    @pytest.mark.parametrize(
        "error",
        [
            _refusal(),
            EgressPolicyApprovalRequiredError(mcp_server_id="s", tool_name="t"),
            RateLimitExceeded(limit=1, window_seconds=60),
        ],
        ids=["egress_deny", "approval_required", "rate_limit"],
    )
    def test_leaves_the_span_unset_and_names_the_refusal(self, sdk, error) -> None:
        self._run(sdk, error)

        status, attributes, events = _status_and_attributes(sdk)
        assert status == "UNSET"
        assert attributes[ERROR_TYPE] == type(error).__qualname__
        assert events == [], "an `exception` event reads as a failure too"

    def test_an_ordinary_failure_still_ends_error(self, sdk) -> None:
        self._run(sdk, ToolTimeoutError(mcp_server_id="s", tool_name="t", timeout=1.0))

        status, attributes, events = _status_and_attributes(sdk)
        assert status == "ERROR"
        assert attributes[ERROR_TYPE] == "ToolTimeoutError"
        assert events == ["exception"]


class TestSettlingAFailedCall:
    """`batch.call.<tool>`: the executor handles refusals as data, so nothing escapes."""

    def _settle(self, sdk, outcome: str | None, error_type: str | None = "ToolWithdrawnError"):
        from mcp_hangar.observability.tracing import record_call_outcome, settle_failed_call

        with sdk.tracer.start_as_current_span("batch.call.t") as span:
            if outcome is not None:
                record_call_outcome(outcome)
            settle_failed_call(span, error_type)
        return _status_and_attributes(sdk)

    def test_a_denied_call_stays_unset_and_keeps_its_error_type(self, sdk) -> None:
        status, attributes, _ = self._settle(sdk, Gate.DENY)

        assert status == "UNSET"
        assert attributes[ERROR_TYPE] == "ToolWithdrawnError"
        assert attributes[Gate.CALL_OUTCOME] == Gate.DENY

    @pytest.mark.parametrize("outcome", [Gate.ERROR, None], ids=["error", "outcome_never_recorded"])
    def test_anything_else_ends_error(self, sdk, outcome) -> None:
        status, attributes, _ = self._settle(sdk, outcome, error_type="McpServerStartError")

        assert status == "ERROR", "an unrecorded outcome must fail closed to ERROR, as before"
        assert attributes[ERROR_TYPE] == "McpServerStartError"

    def test_an_unshaped_error_type_is_bounded_on_the_refusal_path_too(self, sdk) -> None:
        _, attributes, _ = self._settle(sdk, Gate.DENY, error_type="a whole sentence, with commas")

        assert attributes[ERROR_TYPE] == "_OTHER"


class TestTheDispatchSpan:
    """Any refusal is `rejected`, not the rate limit alone."""

    @dataclass
    class _Command:
        value: int = 1

    class _Handler:
        def __init__(self, error: Exception) -> None:
            self.error = error

        def handle(self, message: Any) -> str:
            raise self.error

    def _dispatch(self, sdk, error: Exception):
        from mcp_hangar.infrastructure.command_bus import CommandBus

        bus = CommandBus()
        bus.register(self._Command, self._Handler(error))
        with pytest.raises(type(error)):
            bus.send(self._Command())
        dispatch = next(s for s in sdk.get_finished_spans() if s.name == "dispatch._Command")
        return dispatch.status.status_code.name, dict(dispatch.attributes or {})

    def test_an_l7_denial_raised_by_the_handler_is_rejected(self, sdk) -> None:
        """It read `error` before: only `RateLimitExceeded` was caught as a refusal."""
        status, attributes = self._dispatch(sdk, _refusal())

        assert attributes[Dispatch.OUTCOME] == Dispatch.REJECTED
        assert attributes[ERROR_TYPE] == "EgressPolicyDeniedError"
        assert status == "UNSET"

    def test_a_broken_handler_is_still_an_error(self, sdk) -> None:
        status, attributes = self._dispatch(sdk, RuntimeError("handler broke"))

        assert attributes[Dispatch.OUTCOME] == Dispatch.ERROR
        assert status == "ERROR"


def test_every_dispatch_refusal_the_executor_knows_carries_the_marker() -> None:
    """The executor's summary and the tracer's status must classify the same exceptions.

    `_REFUSED_AT_DISPATCH` is a set of names, because a `CallResult` carries the
    type's name and not the exception. This ties it to `ExpectedRefusal`, so a
    new refusal cannot end up `deny` on one side and `error` on the other.
    """
    from mcp_hangar.server.tools.batch.executor import _REFUSED_AT_DISPATCH

    marked = {
        name
        for name, obj in inspect.getmembers(domain_exceptions, inspect.isclass)
        if issubclass(obj, ExpectedRefusal) and obj is not ExpectedRefusal
    }

    assert marked == set(_REFUSED_AT_DISPATCH)


class TestTheExecutorEndToEnd:
    """The whole path, through the gates the executor really runs.

    Reuses the #1285 harness: the same fixtures, the same arrangement helpers,
    the same in-memory exporter patched into the executor.
    """

    def test_a_gate_deny_leaves_the_call_span_unset(self, ctx, executor_exporter) -> None:
        with ExitStack() as stack:
            gate_decisions._arrange(ctx, "withdrawal", stack)
            assert _run().success is False

        span = gate_decisions._call_span(executor_exporter)
        assert span.attributes[Gate.CALL_OUTCOME] == Gate.DENY
        assert span.attributes[Gate.REFUSAL_GATE] == "withdrawal"
        assert span.status.status_code.name == "UNSET", "a refusal is not an error trace"
        assert span.attributes[ERROR_TYPE] == "ToolWithdrawnError", "what refused is still named"

    def test_a_failed_cold_start_still_ends_error(self, ctx, executor_exporter) -> None:
        gate_decisions._arrange_catalogue()
        ctx.get_mcp_server.return_value.state.value = "cold"
        ctx.command_bus.send.side_effect = RuntimeError("boom")

        assert _run().error_type == "McpServerStartError"

        span = gate_decisions._call_span(executor_exporter)
        assert span.attributes[Gate.CALL_OUTCOME] == Gate.ERROR
        assert span.status.status_code.name == "ERROR", "the gate machinery broke; that is a failure"
        assert span.attributes[ERROR_TYPE] == "McpServerStartError"
