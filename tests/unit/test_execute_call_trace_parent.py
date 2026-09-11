"""``BatchExecutor._execute_call`` parents its call span on a carrier it is handed.

The direct-executor path: no ambient span, a carrier in ``call.metadata``. That
is what a caller of the executor outside the served app sees, and #1270 keeps it
("direct executor call with no ambient span and a valid carrier: batch.call is
parented on the carrier"). The served path -- where the SDK already bound the
caller's ``_meta`` -- is asserted over the real app in
``tests/integration/test_trace_propagation_e2e.py``; these units cannot prove it,
because they patch the tracer and the application context.

The call is refused (the mock context knows no server); only the span it opened
is asserted. Moved here from the integration file, which called itself
end-to-end while never reaching an upstream (#1284).
"""

import threading
import time
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


def _execute(provider, call_spec) -> None:
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
        )


def _call_spans(exporter, tool: str) -> list:
    return [s for s in exporter.get_finished_spans() if s.name == f"batch.call.{tool}"]


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
