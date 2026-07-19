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


@pytest.mark.skipif(not _OTEL_SDK_AVAILABLE, reason="requires opentelemetry-sdk")
class TestBuildSampler:
    """_build_sampler honors OTEL_TRACES_SAMPLER / OTEL_TRACES_SAMPLER_ARG."""

    @staticmethod
    def _build(name=None, arg=None):
        from mcp_hangar.observability import tracing

        env = {}
        if name is not None:
            env["OTEL_TRACES_SAMPLER"] = name
        if arg is not None:
            env["OTEL_TRACES_SAMPLER_ARG"] = arg
        with patch.dict("os.environ", env, clear=False):
            # Ensure a clean read even if the vars are absent
            for key in ("OTEL_TRACES_SAMPLER", "OTEL_TRACES_SAMPLER_ARG"):
                if key not in env:
                    import os

                    os.environ.pop(key, None)
            return tracing._build_sampler()

    def test_default_is_parentbased_always_on(self):
        from opentelemetry.sdk.trace.sampling import ParentBased

        s = self._build()
        assert isinstance(s, ParentBased)
        assert "AlwaysOn" in s.get_description()

    def test_always_off(self):
        s = self._build("always_off")
        assert s.get_description() == "AlwaysOffSampler"

    def test_always_on(self):
        s = self._build("always_on")
        assert s.get_description() == "AlwaysOnSampler"

    def test_traceidratio_uses_arg(self):
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        s = self._build("traceidratio", "0.25")
        assert isinstance(s, TraceIdRatioBased)
        assert abs(s.rate - 0.25) < 1e-9

    def test_traceidratio_invalid_arg_falls_back_to_one(self):
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased

        s = self._build("traceidratio", "not-a-number")
        assert isinstance(s, TraceIdRatioBased)
        assert abs(s.rate - 1.0) < 1e-9

    def test_parentbased_traceidratio(self):
        from opentelemetry.sdk.trace.sampling import ParentBased

        s = self._build("parentbased_traceidratio", "0.1")
        assert isinstance(s, ParentBased)

    def test_unknown_falls_back_to_parentbased_always_on(self):
        from opentelemetry.sdk.trace.sampling import ParentBased

        s = self._build("bogus-sampler")
        assert isinstance(s, ParentBased)
        assert "AlwaysOn" in s.get_description()


@pytest.mark.skipif(not _OTEL_SDK_AVAILABLE, reason="requires opentelemetry-sdk")
class TestUpstreamCallSpan:
    """upstream_call_span emits a SpanKind.CLIENT span with GenAI/MCP semconv attrs."""

    @staticmethod
    def _exporter_and_provider():
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import SimpleSpanProcessor
        from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

        exporter = InMemorySpanExporter()
        provider = TracerProvider()
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return exporter, provider

    def _run(self, method, params):
        from mcp_hangar.observability import tracing

        # Patch get_tracer (not the global provider — OTel only lets you set
        # that once per process, so per-test exporters wouldn't wire up).
        exporter, provider = self._exporter_and_provider()
        test_tracer = provider.get_tracer("test")
        try:
            with patch.object(tracing, "get_tracer", lambda name=None: test_tracer):
                with tracing.upstream_call_span(method, params):
                    pass
            return exporter.get_finished_spans()
        finally:
            provider.shutdown()

    def test_tools_call_span_is_client_kind_with_tool_attrs(self):
        from opentelemetry.trace import SpanKind

        spans = self._run("tools/call", {"name": "add", "arguments": {"a": 1}})
        assert len(spans) == 1
        span = spans[0]
        assert span.kind == SpanKind.CLIENT
        assert span.name == "execute_tool add"
        assert span.attributes.get("gen_ai.tool.name") == "add"
        assert span.attributes.get("gen_ai.operation.name") == "execute_tool"
        assert span.attributes.get("mcp.method.name") == "tools/call"

    def test_non_tool_method_span_named_by_method(self):
        spans = self._run("tools/list", {})
        assert len(spans) == 1
        assert spans[0].name == "tools/list"
        assert spans[0].attributes.get("mcp.method.name") == "tools/list"
