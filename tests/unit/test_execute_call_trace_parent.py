"""Where ``BatchExecutor._execute_call`` parents its call span (#1270).

An ambient span -- ``batch.execute`` in the worker, under the SDK's SERVER span
that already bound the caller's ``_meta`` -- is the parent. A carrier from
``_meta`` or ``call.metadata`` parents the call span only when there is no
ambient span: the direct-executor path, what a caller of the executor outside
the served app sees. A carrier naming another trace than the ambient span is
kept as a link. The served path is asserted over the real app in
``tests/integration/test_trace_propagation_e2e.py``; these units cannot prove
it, because they patch the tracer and the application context.

The call is refused (the mock context knows no server); only the span it opened
is asserted. Moved here from the integration file, which called itself
end-to-end while never reaching an upstream (#1284).
"""

from types import SimpleNamespace
import threading
import time
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.otel_sdk


@pytest.fixture()
def otel_setup():
    """A local TracerProvider + InMemorySpanExporter, never registered globally."""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    yield exporter, provider
    exporter.clear()


def _execute(provider, call_spec, request_ctx: Any = None) -> None:
    from mcp_hangar.server.tools.batch.executor import BatchExecutor

    mock_ctx = MagicMock()
    mock_ctx.get_mcp_server.return_value = None
    mock_ctx.mcp_server_exists.return_value = False

    test_tracer = provider.get_tracer("mcp_hangar.server.tools.batch.executor")
    with (
        patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=mock_ctx),
        patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=test_tracer),
        # extract_trace_context is gated on Hangar's own init having run.
        patch("mcp_hangar.observability.tracing._initialized", True),
    ):
        BatchExecutor()._execute_call(
            call_spec,
            cancel_event=threading.Event(),
            global_timeout=60.0,
            batch_start_time=time.perf_counter(),
            request_ctx=request_ctx,
        )


def _call_spans(exporter, tool: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{tool}"]


# A caller's span in a trace no local span belongs to.
OTHER_TRACE_ID = 0x1270_0000_0000_0000_0000_0000_0000_0001
OTHER_SPAN_ID = 0x1270_0000_0000_0001
OTHER_CARRIER = {"traceparent": f"00-{OTHER_TRACE_ID:032x}-{OTHER_SPAN_ID:016x}-01"}


def _request_ctx(meta: Any) -> SimpleNamespace:
    """A FastMCP request context whose inbound ``params._meta`` is ``meta``."""
    return SimpleNamespace(request_context=SimpleNamespace(meta=meta))


def _call(tool: str, metadata: dict[str, str] | None = None):
    from mcp_hangar.server.tools.batch.models import CallSpec

    return CallSpec(index=0, call_id=f"test-{tool}", mcp_server="math", tool=tool, arguments={}, metadata=metadata)


def test_a_carrier_in_call_metadata_parents_the_call_span(otel_setup) -> None:
    exporter, provider = otel_setup

    from opentelemetry.propagate import inject

    from mcp_hangar.server.tools.batch.models import CallSpec

    carrier: dict[str, str] = {}
    with provider.get_tracer("test-agent").start_as_current_span("test-agent-span") as agent_span:
        agent_ctx = agent_span.get_span_context()
        inject(carrier)
    assert "traceparent" in carrier, "W3C TraceContext must be injectable"
    exporter.clear()

    call_spec = CallSpec(
        index=0, call_id="test-call-parent", mcp_server="math", tool="add", arguments={"a": 1, "b": 2}, metadata=carrier
    )
    _execute(provider, call_spec)

    [span] = _call_spans(exporter, "add")
    assert span.get_span_context().trace_id == agent_ctx.trace_id
    assert span.parent is not None
    assert span.parent.span_id == agent_ctx.span_id


def test_without_a_carrier_the_call_span_is_a_root(otel_setup) -> None:
    exporter, provider = otel_setup

    from mcp_hangar.server.tools.batch.models import CallSpec

    call_spec = CallSpec(
        index=0, call_id="test-call-root", mcp_server="math", tool="multiply", arguments={"a": 2, "b": 3}, metadata={}
    )
    _execute(provider, call_spec)

    [span] = _call_spans(exporter, "multiply")
    assert span.parent is None, "a call span opened without a carrier or ambient span must be a root"


def test_the_ambient_span_parents_the_call_span_over_the_carrier_it_descends_from(otel_setup) -> None:
    """The served shape: the SDK's SERVER span was opened from ``_meta``, and the
    same ``_meta`` reaches the executor. The local span wins; the remote caller
    is already its ancestor, so no link repeats it."""
    exporter, provider = otel_setup

    from opentelemetry.propagate import extract

    carrier = {"traceparent": f"00-{0x1270_0001:032x}-{0x5EED_0001:016x}-01"}
    with provider.get_tracer("sdk").start_as_current_span("batch.execute", context=extract(carrier)) as local:
        _execute(provider, _call("add"), request_ctx=_request_ctx(carrier))

    [span] = _call_spans(exporter, "add")
    assert span.parent is not None
    assert span.parent.span_id == local.get_span_context().span_id
    assert span.get_span_context().trace_id == 0x1270_0001
    assert list(span.links) == []


@pytest.mark.parametrize("meta", [None, {}, {"traceparent": "not-a-traceparent"}], ids=["none", "absent", "invalid"])
def test_with_no_valid_carrier_the_ambient_span_parents_the_call_span(otel_setup, meta) -> None:
    exporter, provider = otel_setup

    with provider.get_tracer("sdk").start_as_current_span("batch.execute") as local:
        _execute(provider, _call("add"), request_ctx=_request_ctx(meta))

    [span] = _call_spans(exporter, "add")
    assert span.parent is not None, "an empty carrier must not detach the call span into a root"
    assert span.parent.span_id == local.get_span_context().span_id
    assert list(span.links) == []


@pytest.mark.parametrize("source", ["meta", "call_metadata"])
def test_a_carrier_from_another_trace_is_a_link_not_the_parent(otel_setup, source) -> None:
    exporter, provider = otel_setup

    request_ctx = _request_ctx(OTHER_CARRIER if source == "meta" else None)
    call = _call("add", metadata=OTHER_CARRIER if source == "call_metadata" else None)
    with provider.get_tracer("sdk").start_as_current_span("batch.execute") as local:
        _execute(provider, call, request_ctx=request_ctx)

    [span] = _call_spans(exporter, "add")
    local_ctx = local.get_span_context()
    assert span.parent is not None
    assert span.parent.span_id == local_ctx.span_id
    assert span.get_span_context().trace_id == local_ctx.trace_id
    [link] = span.links
    assert (link.context.trace_id, link.context.span_id) == (OTHER_TRACE_ID, OTHER_SPAN_ID)
    assert link.context.is_remote


def test_with_no_ambient_span_a_meta_carrier_parents_the_call_span(otel_setup) -> None:
    exporter, provider = otel_setup

    _execute(provider, _call("add"), request_ctx=_request_ctx(OTHER_CARRIER))

    [span] = _call_spans(exporter, "add")
    assert span.parent is not None
    assert (span.parent.trace_id, span.parent.span_id) == (OTHER_TRACE_ID, OTHER_SPAN_ID)
    assert list(span.links) == []
