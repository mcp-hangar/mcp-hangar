"""Integration test: end-to-end W3C TraceContext propagation.

Verifies the full propagation chain:
  Agent (provides traceparent) -> BatchExecutor (extracts, creates child span)

Expected span hierarchy:
  [agent-root-span]
      +-- [batch.call.{tool}] (created by BatchExecutor with parent = agent-root)

Uses InMemorySpanExporter -- no external collector required.

Note: OTEL only allows set_tracer_provider() once per process. These tests
use patch to inject a controlled TracerProvider into the get_tracer() call
path, avoiding interference with other test modules.
"""

import threading
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def otel_setup():
    """Create a fresh TracerProvider + InMemorySpanExporter per test.

    Patches get_tracer in the executor module to use our provider directly,
    avoiding the OTEL global set_tracer_provider (which can only be set once).
    """
    pytest.importorskip("opentelemetry.sdk.trace")

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    yield exporter, provider

    exporter.clear()


class TestEndToEndTracePropagation:
    """Agent traceparent -> BatchExecutor -> child span correlated in one trace."""

    def test_batch_executor_creates_child_span_from_agent_traceparent(self, otel_setup) -> None:
        """
        Given an agent request with a traceparent, BatchExecutor creates a child span.

        Span hierarchy:
          [test-agent-span] (root, simulating agent)
              +-- [batch.call.add] (child, created by BatchExecutor)
        """
        exporter, provider = otel_setup

        from opentelemetry.propagate import inject
        from mcp_hangar.server.tools.batch.executor import BatchExecutor
        from mcp_hangar.server.tools.batch.models import CallSpec

        executor = BatchExecutor()

        # Step 1: Simulate agent creating a root span and injecting traceparent.
        # Use our test provider directly (not the global one).
        tracer = provider.get_tracer("test-agent")
        agent_span_id = None
        agent_trace_id = None
        carrier: dict[str, str] = {}

        with tracer.start_as_current_span("test-agent-span") as agent_span:
            ctx = agent_span.get_span_context()
            agent_span_id = ctx.span_id
            agent_trace_id = ctx.trace_id
            inject(carrier)  # inject traceparent into carrier dict

        # carrier now has {"traceparent": "00-<trace_id>-<span_id>-01"}
        assert "traceparent" in carrier, "W3C TraceContext must be injectable"

        # Step 2: Submit batch call with agent's traceparent in metadata.
        call_spec = CallSpec(
            index=0,
            call_id="test-call-e2e",
            mcp_server="math",
            tool="add",
            arguments={"a": 1, "b": 2},
            metadata=carrier,  # contains traceparent
        )

        mock_ctx = MagicMock()
        mock_ctx.get_mcp_server.return_value = None
        mock_ctx.mcp_server_exists.return_value = False

        # Clear the agent span from exporter so we only see BatchExecutor spans
        exporter.clear()

        # Patch get_tracer in the executor module to return our test provider's tracer.
        # Patch _initialized so extract_trace_context runs for real.
        test_tracer = provider.get_tracer("mcp_hangar.server.tools.batch.executor")
        with (
            patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=mock_ctx),
            patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=test_tracer),
            patch("mcp_hangar.observability.tracing._initialized", True),
        ):
            executor._execute_call(
                call_spec,
                cancel_event=threading.Event(),
                global_timeout=60.0,
                batch_start_time=0.0,
            )

        # Step 3: Verify span hierarchy
        finished_spans = exporter.get_finished_spans()
        batch_spans = [s for s in finished_spans if "add" in s.name or "batch" in s.name.lower()]

        assert len(batch_spans) >= 1, (
            f"Expected at least one batch/tool span, got spans: {[s.name for s in finished_spans]}"
        )

        batch_span = batch_spans[0]

        # The batch span's trace_id must match the agent's trace_id
        batch_ctx = batch_span.get_span_context()
        assert batch_ctx.trace_id == agent_trace_id, (
            f"Batch span trace_id {batch_ctx.trace_id:032x} must match agent trace_id {agent_trace_id:032x}"
        )

        # The batch span's parent_span_id must be the agent span
        assert batch_span.parent is not None, "Batch span must have a parent (agent span)"
        assert batch_span.parent.span_id == agent_span_id, (
            f"Batch span parent_id {batch_span.parent.span_id:016x} must match agent span_id {agent_span_id:016x}"
        )

    def test_batch_executor_creates_root_span_without_traceparent(self, otel_setup) -> None:
        """Without traceparent, BatchExecutor creates a new root span (no crash)."""
        exporter, provider = otel_setup

        from mcp_hangar.server.tools.batch.executor import BatchExecutor
        from mcp_hangar.server.tools.batch.models import CallSpec

        executor = BatchExecutor()
        call_spec = CallSpec(
            index=0,
            call_id="test-call-root",
            mcp_server="math",
            tool="multiply",
            arguments={"a": 2, "b": 3},
            metadata={},
        )

        mock_ctx = MagicMock()
        mock_ctx.get_mcp_server.return_value = None
        mock_ctx.mcp_server_exists.return_value = False

        test_tracer = provider.get_tracer("mcp_hangar.server.tools.batch.executor")
        with (
            patch("mcp_hangar.server.tools.batch.executor.get_context", return_value=mock_ctx),
            patch("mcp_hangar.server.tools.batch.executor.get_tracer", return_value=test_tracer),
            patch("mcp_hangar.observability.tracing._initialized", True),
        ):
            executor._execute_call(
                call_spec,
                cancel_event=threading.Event(),
                global_timeout=60.0,
                batch_start_time=0.0,
            )

        finished_spans = exporter.get_finished_spans()
        tool_spans = [s for s in finished_spans if "multiply" in s.name or "batch" in s.name.lower()]
        assert len(tool_spans) >= 1, (
            f"Span must be created even without traceparent, got: {[s.name for s in finished_spans]}"
        )

        # Root span has no parent
        root_span = tool_spans[0]
        assert root_span.parent is None, "Span created without traceparent must be a root span"
