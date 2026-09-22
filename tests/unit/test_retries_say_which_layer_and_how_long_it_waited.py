"""A retried call says which layer retried it, how often, and how long it waited (#1287).

Two layers retry one call and both reported totals only. The executor's
`invoke_with_retry` carried an attempt count; the HTTP client resent inside one
CLIENT span where only a counter could see it. So three upstream POSTs might be
one executor attempt that resent twice, or three executor attempts, and the
trace read the same either way -- while "this call took nine seconds" could not
be split into the attempts that failed and the backoff that was waited out.

The executor half is driven through `BatchExecutor` against a command bus that
fails and then succeeds, not through `retry_sync` in isolation: the attempt
index has to survive the closure, the span nesting and the thread pool, and a
test of the primitive alone would prove none of that.
"""

from __future__ import annotations

from unittest.mock import Mock, patch

import pytest

from mcp_hangar.observability.conventions import Retry
from mcp_hangar.retry import RetryAttempt, RetryPolicy, RetryResult
from mcp_hangar.server.tools.batch.executor import BatchExecutor, _retry_outcome
from mcp_hangar.server.tools.batch.models import CallSpec

pytestmark = pytest.mark.otel_sdk


@pytest.fixture
def spans():
    """A real TracerProvider whose spans land in memory, patched into the executor."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")

    with patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=tracer):
        yield exporter


@pytest.fixture
def world(monkeypatch):
    """A ready `math` server, a two-attempt policy, and no real sleeping."""
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()

    server = Mock()
    server.state.value = "ready"
    server.has_tools = False
    server.health.should_degrade.return_value = False
    ctx.get_mcp_server.side_effect = lambda key: server if key == "math" else None
    ctx.mcp_server_exists.side_effect = lambda key: key == "math"

    monkeypatch.setattr("mcp_hangar.retry.time.sleep", lambda _seconds: None)

    with (
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch(
            "mcp_hangar.server.tools.batch.executor._retry_policy_for",
            return_value=RetryPolicy(max_attempts=3, initial_delay=0.25, jitter=False),
        ),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as groups,
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        groups.get.return_value = None
        exec_groups.get.return_value = None
        yield ctx


def _run(ctx) -> object:
    return BatchExecutor().execute(
        batch_id="batch-1",
        calls=[CallSpec(index=0, call_id="call-1", mcp_server="math", tool="add", arguments={"a": 1})],
        max_concurrency=1,
        global_timeout=60.0,
        fail_fast=False,
    )


def _named(exporter, name: str) -> list:
    return [span for span in exporter.get_finished_spans() if span.name == name]


def _retry_events(exporter) -> list:
    return [
        event for span in exporter.get_finished_spans() for event in span.events if event.name == "hangar.retry.attempt"
    ]


def test_a_call_that_succeeds_on_the_second_attempt_says_so(world, spans) -> None:
    """Both attempts are indexed, the failed one is an event, the outcome is success."""
    world.command_bus.send.side_effect = [ConnectionError("upstream down"), {"result": 42}]

    result = _run(world)

    assert result.succeeded == 1
    indexes = sorted(span.attributes[Retry.INDEX] for span in _named(spans, "command.send.InvokeToolCommand"))
    assert indexes == [1, 2], indexes

    events = _retry_events(spans)
    assert len(events) == 1, events
    assert events[0].attributes[Retry.LAYER] == "executor"
    assert events[0].attributes[Retry.INDEX] == 1
    assert events[0].attributes[Retry.REASON] == "ConnectionError"
    assert events[0].attributes[Retry.BACKOFF_S] == pytest.approx(0.25)

    [retry_span] = _named(spans, "invoke_with_retry")
    assert retry_span.attributes[Retry.OUTCOME] == Retry.SUCCESS


def test_the_upstream_posts_equal_the_reported_attempts(world, spans) -> None:
    """The layers are distinguishable only if the counts reconcile."""
    world.command_bus.send.side_effect = [ConnectionError("down"), ConnectionError("down"), {"result": 42}]

    _run(world)

    sends = _named(spans, "command.send.InvokeToolCommand")
    events = _retry_events(spans)
    # Three attempts, two of which were retried: one event per retry, never per
    # attempt, so the arithmetic an operator does on a trace holds.
    assert len(sends) == 3
    assert len(events) == 2
    assert [event.attributes[Retry.INDEX] for event in events] == [1, 2]
    assert all(event.attributes[Retry.LAYER] == "executor" for event in events)


def test_an_exhausted_call_is_not_a_non_retryable_one(world, spans) -> None:
    """Two failures that read the same to a counter, and differently to a reader."""
    world.command_bus.send.side_effect = ConnectionError("still down")

    result = _run(world)

    assert result.failed == 1
    [retry_span] = _named(spans, "invoke_with_retry")
    assert retry_span.attributes[Retry.OUTCOME] == Retry.EXHAUSTED
    assert len(_retry_events(spans)) == 2, "two retries before the third attempt gave up"


def test_a_refusal_is_never_retried_and_says_non_retryable(world, spans) -> None:
    """The answer was the point: re-asking a gate is not recovering from a blip."""
    from mcp_hangar.domain.exceptions import EgressPolicyDeniedError

    world.command_bus.send.side_effect = EgressPolicyDeniedError("math", "add", "denied by policy")

    result = _run(world)

    assert result.failed == 1
    assert _named(spans, "command.send.InvokeToolCommand"), "the call was never attempted"
    assert len(_named(spans, "command.send.InvokeToolCommand")) == 1, "a refusal must not be retried"
    assert not _retry_events(spans)
    [retry_span] = _named(spans, "invoke_with_retry")
    assert retry_span.attributes[Retry.OUTCOME] == Retry.NON_RETRYABLE


def test_outcome_tells_exhausted_from_non_retryable() -> None:
    """`retry_sync` records an attempt only when it is about to retry.

    So an empty attempt list on a failure is exactly the error that was never
    worth retrying -- which is the distinction the attribute exists to make.
    """
    attempt = RetryAttempt(attempt_number=1, error_type="ConnectionError", error_message="x", delay_before=0.1)

    assert _retry_outcome(RetryResult(success=True)) == Retry.SUCCESS
    assert _retry_outcome(RetryResult(success=False, attempts=[attempt])) == Retry.EXHAUSTED
    assert _retry_outcome(RetryResult(success=False, attempts=[])) == Retry.NON_RETRYABLE


def test_recording_a_retry_never_breaks_the_retry(monkeypatch) -> None:
    """Telemetry that can fail a retry would be a worse bug than the one it describes."""
    from mcp_hangar.observability import tracing as tracing_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("no tracer today")

    monkeypatch.setattr("opentelemetry.trace.get_current_span", explode)

    tracing_module.record_retry_attempt(Retry.LAYER_EXECUTOR, 1, "ConnectionError", 0.25)
