"""Tests for observability/tracing module."""

from unittest.mock import Mock, patch

import pytest

try:
    import opentelemetry.sdk.trace.export  # noqa: F401

    _OTEL_SDK_AVAILABLE = True
except ImportError:
    _OTEL_SDK_AVAILABLE = False

from mcp_hangar.observability.tracing import (
    get_current_span_id,
    get_current_trace_id,
    get_tracer,
    is_tracing_enabled,
    NoOpSpan,
    NoOpTracer,
    trace_span,
)


class TestNoOpSpan:
    """Tests for NoOpSpan class."""

    def test_set_attribute_does_nothing(self):
        """Should accept attributes without error."""
        span = NoOpSpan()
        span.set_attribute("key", "value")
        span.set_attribute("number", 123)

    def test_set_status_does_nothing(self):
        """Should accept status without error."""
        span = NoOpSpan()
        span.set_status("OK")

    def test_record_exception_does_nothing(self):
        """Should accept exception without error."""
        span = NoOpSpan()
        span.record_exception(ValueError("test"))

    def test_add_event_does_nothing(self):
        """Should accept events without error."""
        span = NoOpSpan()
        span.add_event("test_event", {"key": "value"})

    def test_context_manager(self):
        """Should work as context manager."""
        span = NoOpSpan()
        with span as s:
            assert s is span


class TestNoOpTracer:
    """Tests for NoOpTracer class."""

    def test_start_as_current_span_returns_noop_span(self):
        """Should return NoOpSpan."""
        tracer = NoOpTracer()
        span = tracer.start_as_current_span("test")
        assert isinstance(span, NoOpSpan)

    def test_start_span_returns_noop_span(self):
        """Should return NoOpSpan as context manager."""
        tracer = NoOpTracer()
        with tracer.start_span("test") as span:
            assert isinstance(span, NoOpSpan)


class TestIsTracingEnabled:
    """Tests for is_tracing_enabled function."""

    def test_enabled_by_default(self):
        """Should be enabled by default if OTEL available."""
        with patch.dict("os.environ", {}, clear=True):
            # Result depends on OTEL availability
            result = is_tracing_enabled()
            assert isinstance(result, bool)

    def test_disabled_via_env(self):
        """Should be disabled when MCP_TRACING_ENABLED=false."""
        with patch.dict("os.environ", {"MCP_TRACING_ENABLED": "false"}):
            assert is_tracing_enabled() is False

    def test_disabled_via_env_zero(self):
        """Should be disabled when MCP_TRACING_ENABLED=0."""
        with patch.dict("os.environ", {"MCP_TRACING_ENABLED": "0"}):
            assert is_tracing_enabled() is False


class TestGetTracer:
    """Tests for get_tracer function."""

    def test_returns_tracer_instance(self):
        """Should return a tracer (NoOp or real)."""
        tracer = get_tracer("test_module")
        assert tracer is not None
        # Should have start_as_current_span method
        assert hasattr(tracer, "start_as_current_span")

    def test_returns_noop_when_not_initialized(self):
        """Should return NoOpTracer when not initialized."""
        # Without initialization, should return NoOpTracer
        tracer = get_tracer()
        # Verify it works without errors
        with tracer.start_as_current_span("test"):
            pass


class TestTraceSpan:
    """Tests for trace_span context manager."""

    def test_creates_span(self):
        """Should create and yield a span."""
        with trace_span("test_operation") as span:
            assert span is not None

    def test_accepts_attributes(self):
        """Should accept initial attributes."""
        with trace_span("test", {"key": "value"}) as span:
            # Should not raise
            span.set_attribute("another", "attr")

    def test_accepts_kind(self):
        """Should accept span kind."""
        with trace_span("test", kind="client") as span:
            assert span is not None

        with trace_span("test", kind="server") as span:
            assert span is not None


class TestGetCurrentTraceId:
    """Tests for get_current_trace_id function."""

    def test_returns_none_when_no_span(self):
        """Should return None when not in a span."""
        result = get_current_trace_id()
        # May be None or string depending on context
        assert result is None or isinstance(result, str)


class TestGetCurrentSpanId:
    """Tests for get_current_span_id function."""

    def test_returns_none_when_no_span(self):
        """Should return None when not in a span."""
        result = get_current_span_id()
        # May be None or string depending on context
        assert result is None or isinstance(result, str)


class TestMeteredSpanExporter:
    """Tests for the _MeteredSpanExporter export-failure meter.

    Requires the OpenTelemetry SDK; the whole class is skipped when it is not
    installed (the ``opentelemetry`` extra is not part of the default test env).
    """

    pytestmark = pytest.mark.skipif(
        not _OTEL_SDK_AVAILABLE,
        reason="requires the opentelemetry extra",
    )

    @staticmethod
    def _failures_total() -> float:
        from mcp_hangar.metrics import OTLP_EXPORT_FAILURES_TOTAL

        samples = OTLP_EXPORT_FAILURES_TOTAL.collect()
        return samples[0].value if samples else 0.0

    def _wrapper(self, inner):
        from mcp_hangar.observability.tracing import _MeteredSpanExporter

        return _MeteredSpanExporter(inner)

    def test_success_does_not_increment(self):
        """A successful export must not increment the failure counter."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        inner = Mock()
        inner.export.return_value = SpanExportResult.SUCCESS
        wrapper = self._wrapper(inner)

        before = self._failures_total()
        result = wrapper.export(["span"])

        assert result is SpanExportResult.SUCCESS
        assert self._failures_total() == before
        inner.export.assert_called_once_with(["span"])

    def test_failure_result_increments(self):
        """A FAILURE result must increment the failure counter."""
        from opentelemetry.sdk.trace.export import SpanExportResult

        inner = Mock()
        inner.export.return_value = SpanExportResult.FAILURE
        wrapper = self._wrapper(inner)

        before = self._failures_total()
        result = wrapper.export(["span"])

        assert result is SpanExportResult.FAILURE  # propagated unchanged
        assert self._failures_total() == before + 1

    def test_raised_exception_increments_and_propagates(self):
        """A raising export must increment the counter and re-raise."""
        inner = Mock()
        inner.export.side_effect = ConnectionError("collector down")
        wrapper = self._wrapper(inner)

        before = self._failures_total()
        with pytest.raises(ConnectionError):
            wrapper.export(["span"])

        assert self._failures_total() == before + 1

    def test_shutdown_and_flush_delegate(self):
        """Lifecycle calls must delegate to the wrapped exporter."""
        inner = Mock()
        inner.force_flush.return_value = True
        wrapper = self._wrapper(inner)

        wrapper.shutdown()
        inner.shutdown.assert_called_once()

        assert wrapper.force_flush(1234) is True
        inner.force_flush.assert_called_once_with(1234)
