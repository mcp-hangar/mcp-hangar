"""A trace says what happened to a call's payload on the way through (#1298).

Request and response mutation, the per-call size limit and batch truncation all
changed what a caller got back, and none of them left anything in a trace. They
now record timing, sizes and counts -- never a payload, a cache entry or a
continuation id -- as span events on `batch.call.<tool>`, and batch truncation
as its own `batch.truncate` span.

Every test drives `BatchExecutor.execute` with a real SDK exporter behind
`_TextFreeTracer`, the wrapper `get_tracer()` returns in production, so the
failure outcome asserted here is the one an operator sees. Truncation runs the
real `TruncationManager` with a small budget.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from unittest.mock import Mock, patch

import pytest
from opentelemetry.trace import StatusCode

from mcp_hangar.application.services.mutator_pipeline import MutatorPipeline
from mcp_hangar.domain.contracts.mutator import MutationContext, MutationResult
from mcp_hangar.observability.conventions import Shaping
from mcp_hangar.server.bootstrap.truncation import init_truncation, reset_truncation
from mcp_hangar.server.tools.batch import MAX_RESPONSE_SIZE_BYTES, BatchExecutor, CallSpec

_TOOL = "add"


class _Mutator:
    """A mutator for `tools/call` that rewrites the payload, leaves it, or fails."""

    def __init__(self, *, rewrite: dict[str, Any] | None = None, fail: bool = False) -> None:
        self._rewrite = rewrite
        self._fail = fail

    @property
    def priority_hint(self) -> int:
        return 0

    @property
    def applies_to(self) -> frozenset[str]:
        return frozenset({"tools/call"})

    def mutate(self, context: MutationContext) -> MutationResult:
        if self._fail:
            raise RuntimeError("mutator broke")
        if self._rewrite is None:
            return MutationResult(payload=context.payload, changed=False)
        return MutationResult(payload=self._rewrite, changed=True)


@pytest.fixture
def exporter() -> Iterator[Any]:
    """A real TracerProvider behind `_TextFreeTracer`, patched into the executor."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from mcp_hangar.observability.tracing import _TextFreeTracer

    memory = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(memory))
    tracer = _TextFreeTracer(provider.get_tracer("test-1298"))
    with patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=tracer):
        yield memory


def _upstream(returns: dict[str, Any]) -> Iterator[Mock]:
    ctx = Mock()
    ctx.event_bus = Mock()
    ctx.command_bus = Mock()
    ctx.command_bus.send.return_value = returns
    server = Mock()
    server.state.value = "ready"
    server.has_tools = False
    server.health.should_degrade.return_value = False
    ctx.get_mcp_server.side_effect = lambda k: server if k == "math" else None
    ctx.mcp_server_exists.side_effect = lambda k: k == "math"
    with (
        patch("mcp_hangar.server.tools.batch.validator.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.validator.GROUPS") as groups,
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=ctx),
        patch("mcp_hangar.server.tools.batch.executor.GROUPS") as exec_groups,
    ):
        groups.get.return_value = None
        exec_groups.get.return_value = None
        yield ctx


@pytest.fixture
def upstream() -> Iterator[Mock]:
    yield from _upstream({"result": 42})


@pytest.fixture
def oversized_upstream() -> Iterator[Mock]:
    yield from _upstream({"data": "x" * (MAX_RESPONSE_SIZE_BYTES + 1000)})


def _run(executor: BatchExecutor, calls: int = 1) -> Any:
    specs = [
        CallSpec(index=i, call_id=f"call-{i}", mcp_server="math", tool=_TOOL, arguments={"a": i}) for i in range(calls)
    ]
    return executor.execute(batch_id="b-1298", calls=specs, max_concurrency=4, global_timeout=30.0, fail_fast=False)


def _call_spans(exporter: Any) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{_TOOL}"]


def _events(span: Any, name: str) -> list[dict[str, Any]]:
    return [dict(e.attributes) for e in span.events if e.name == name]


def test_the_default_empty_pipeline_records_no_mutation(exporter: Any, upstream: Mock) -> None:
    result = _run(BatchExecutor())

    assert result.succeeded == 1
    [span] = _call_spans(exporter)
    assert _events(span, Shaping.MUTATION_EVENT) == []


def test_request_and_response_mutation_each_record_an_event(exporter: Any, upstream: Mock) -> None:
    pipeline = MutatorPipeline()
    pipeline.register(_Mutator(rewrite={"rewritten": True}))

    result = _run(BatchExecutor(mutator_pipeline=pipeline))

    assert result.succeeded == 1
    [span] = _call_spans(exporter)
    events = _events(span, Shaping.MUTATION_EVENT)
    assert [e[Shaping.DIRECTION] for e in events] == ["request", "response"]
    assert all(e[Shaping.CHANGED] is True for e in events)
    assert all(e[Shaping.DURATION_MS] >= 0 for e in events)
    # Timing and a flag only: nothing from either payload reaches the span.
    assert all(set(e) == {Shaping.DIRECTION, Shaping.CHANGED, Shaping.DURATION_MS} for e in events)


def test_a_mutator_that_leaves_the_payload_says_so(exporter: Any, upstream: Mock) -> None:
    pipeline = MutatorPipeline()
    pipeline.register(_Mutator())

    _run(BatchExecutor(mutator_pipeline=pipeline))

    [span] = _call_spans(exporter)
    assert [e[Shaping.CHANGED] for e in _events(span, Shaping.MUTATION_EVENT)] == [False, False]


def test_a_failing_mutator_ends_the_call_span_in_error(exporter: Any, upstream: Mock) -> None:
    """The exception leaves `batch.call.<tool>` through `_TextFreeTracer`: ERROR, typed, no message."""
    pipeline = MutatorPipeline()
    pipeline.register(_Mutator(fail=True))

    result = _run(BatchExecutor(mutator_pipeline=pipeline))

    assert result.failed == 1
    [span] = _call_spans(exporter)
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description is None
    assert span.attributes["error.type"] == "RuntimeError"
    assert all("exception.message" not in e.attributes for e in span.events)


def test_an_oversized_result_records_the_drop(exporter: Any, oversized_upstream: Mock) -> None:
    result = _run(BatchExecutor())

    [call] = result.results
    assert call.truncated_reason == "response_size_exceeded"
    [span] = _call_spans(exporter)
    [drop] = _events(span, Shaping.DROP_EVENT)
    assert drop == {
        Shaping.REASON: "response_size_exceeded",
        Shaping.SIZE_BYTES: call.original_size_bytes,
        Shaping.LIMIT_BYTES: MAX_RESPONSE_SIZE_BYTES,
    }


@pytest.fixture
def small_truncation_budget() -> Iterator[None]:
    reset_truncation()
    init_truncation(
        {"truncation": {"enabled": True, "max_batch_size_bytes": 400, "min_per_response_bytes": 100, "cache_ttl_s": 60}}
    )
    yield
    reset_truncation()


def test_batch_truncation_is_its_own_span_with_counts_only(
    exporter: Any, small_truncation_budget: None, upstream: Mock
) -> None:
    # Call 0 answers small enough to fit its share; the other two do not. The
    # count must be the results truncation cut, not every result it was given.
    upstream.command_bus.send.side_effect = lambda cmd: {"r": 1} if cmd.arguments["a"] == 0 else {"data": "y" * 600}

    result = _run(BatchExecutor(), calls=3)

    cut = [r for r in result.results if r.truncated]
    assert cut, "the budget should have cut something"
    assert len(cut) < len(result.results), "one small result should have been left whole"
    [span] = [s for s in exporter.get_finished_spans() if s.name == "batch.truncate"]
    assert span.attributes[Shaping.TRUNCATED_COUNT] == len(cut)
    assert span.attributes[Shaping.CONTINUATION] is any(r.continuation_id for r in cut)
    assert set(span.attributes) == {Shaping.TRUNCATED_COUNT, Shaping.CONTINUATION}


def test_no_truncation_manager_means_no_truncate_span(exporter: Any, upstream: Mock) -> None:
    reset_truncation()

    _run(BatchExecutor())

    assert [s for s in exporter.get_finished_spans() if s.name == "batch.truncate"] == []
