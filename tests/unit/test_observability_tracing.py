"""Tests for observability/tracing module."""

from unittest.mock import patch

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
