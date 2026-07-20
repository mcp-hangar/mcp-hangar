"""A failed batch tool call must mark its span ERROR (so error traces are findable).

The inner call handles failures as data (CallResult.success=False), so the span
never sees an exception and would otherwise stay UNSET -- a failing call would
look successful in the trace UI.
"""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("opentelemetry.sdk.trace")


def _exporter_tracer():
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return exporter, provider


class TestMarkSpanError:
    def test_sets_error_status(self):
        from opentelemetry.trace import StatusCode

        from mcp_hangar.observability import tracing

        exporter, provider = _exporter_tracer()
        tracer = provider.get_tracer("test")
        with tracer.start_as_current_span("op") as span:
            tracing.mark_span_error(span, "boom")
        assert exporter.get_finished_spans()[0].status.status_code == StatusCode.ERROR

    def test_noop_span_is_safe(self):
        # NoOpSpan (tracing disabled) must not raise.
        from mcp_hangar.observability.tracing import NoOpSpan, mark_span_error

        mark_span_error(NoOpSpan(), "boom")


class TestExecutorMarksFailureSpan:
    def _run(self, *, success: bool):
        from opentelemetry.trace import StatusCode

        import mcp_hangar.server.tools.batch.executor as ex_mod
        from mcp_hangar.server.tools.batch.executor import BatchExecutor

        exporter, provider = _exporter_tracer()
        tracer = provider.get_tracer("test")

        ex = BatchExecutor()
        call = MagicMock()
        call.tool = "divide"
        call.mcp_server = "math"
        call.call_id = "c1"
        call.metadata = None

        result = MagicMock()
        result.success = success
        result.error = None if success else "division by zero"

        with (
            patch.object(ex_mod, "get_tracer", lambda name=None: tracer),
            patch.object(ex_mod, "get_context", lambda: None),
            patch.object(ex, "_execute_call_inner", return_value=result),
        ):
            got = ex._execute_call(call, threading.Event(), 60.0, time.perf_counter())

        assert got is result
        span = next(s for s in exporter.get_finished_spans() if s.name == "batch.call.divide")
        return span.status.status_code, StatusCode

    def test_failure_marks_error(self):
        code, StatusCode = self._run(success=False)
        assert code == StatusCode.ERROR

    def test_success_stays_unset(self):
        code, StatusCode = self._run(success=True)
        assert code in (StatusCode.UNSET, StatusCode.OK)
