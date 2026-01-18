"""Tests for ObservabilityPort and NullObservabilityAdapter.

These tests verify the interface contract and no-op behavior
when observability is disabled.
"""

import pytest

from mcp_hangar.application.ports.observability import (
    NullObservabilityAdapter,
    NullSpanHandle,
    ObservabilityPort,
    SpanData,
    SpanHandle,
    TraceContext,
)


class TestTraceContext:
    """Tests for TraceContext value object."""

    def test_creates_with_required_fields(self) -> None:
        """TraceContext requires only trace_id."""
        ctx = TraceContext(trace_id="trace-123")

        assert ctx.trace_id == "trace-123"
        assert ctx.span_id is None
        assert ctx.user_id is None
        assert ctx.session_id is None

    def test_creates_with_all_fields(self) -> None:
        """TraceContext accepts all optional fields."""
        ctx = TraceContext(
            trace_id="trace-123",
            span_id="span-456",
            user_id="user-789",
            session_id="session-abc",
        )

        assert ctx.trace_id == "trace-123"
        assert ctx.span_id == "span-456"
        assert ctx.user_id == "user-789"
        assert ctx.session_id == "session-abc"

    def test_is_frozen(self) -> None:
        """TraceContext is immutable."""
        ctx = TraceContext(trace_id="trace-123")

        with pytest.raises(AttributeError):
            ctx.trace_id = "modified"  # type: ignore


class TestSpanData:
    """Tests for SpanData."""

    def test_creates_with_defaults(self) -> None:
        """SpanData creates with sensible defaults."""
        data = SpanData(name="test-span")

        assert data.name == "test-span"
        assert data.input_data == {}
        assert data.output_data is None
        assert data.metadata == {}
        assert data.error is None
        assert data.duration_ms is None


class TestNullSpanHandle:
    """Tests for NullSpanHandle no-op implementation."""

    def test_end_success_does_nothing(self) -> None:
        """end_success is a no-op."""
        handle = NullSpanHandle()
        # Should not raise
        handle.end_success({"result": "test"})

    def test_end_error_does_nothing(self) -> None:
        """end_error is a no-op."""
        handle = NullSpanHandle()
        # Should not raise
        handle.end_error(ValueError("test error"))

    def test_set_metadata_does_nothing(self) -> None:
        """set_metadata is a no-op."""
        handle = NullSpanHandle()
        # Should not raise
        handle.set_metadata("key", "value")


class TestNullObservabilityAdapter:
    """Tests for NullObservabilityAdapter no-op implementation."""

    def test_implements_port_interface(self) -> None:
        """NullObservabilityAdapter implements ObservabilityPort."""
        adapter = NullObservabilityAdapter()
        assert isinstance(adapter, ObservabilityPort)

    def test_start_tool_span_returns_null_handle(self) -> None:
        """start_tool_span returns NullSpanHandle."""
        adapter = NullObservabilityAdapter()

        span = adapter.start_tool_span(
            provider_name="math",
            tool_name="add",
            input_params={"a": 1, "b": 2},
        )

        assert isinstance(span, NullSpanHandle)

    def test_start_tool_span_with_context(self) -> None:
        """start_tool_span accepts trace context."""
        adapter = NullObservabilityAdapter()
        context = TraceContext(trace_id="trace-123", user_id="user-456")

        span = adapter.start_tool_span(
            provider_name="math",
            tool_name="add",
            input_params={"a": 1, "b": 2},
            trace_context=context,
        )

        assert isinstance(span, NullSpanHandle)

    def test_record_score_does_nothing(self) -> None:
        """record_score is a no-op."""
        adapter = NullObservabilityAdapter()
        # Should not raise
        adapter.record_score(
            trace_id="trace-123",
            name="latency",
            value=42.5,
            comment="test",
        )

    def test_record_health_check_does_nothing(self) -> None:
        """record_health_check is a no-op."""
        adapter = NullObservabilityAdapter()
        # Should not raise
        adapter.record_health_check(
            provider_name="math",
            healthy=True,
            latency_ms=10.5,
        )

    def test_flush_does_nothing(self) -> None:
        """flush is a no-op."""
        adapter = NullObservabilityAdapter()
        # Should not raise
        adapter.flush()

    def test_shutdown_does_nothing(self) -> None:
        """shutdown is a no-op."""
        adapter = NullObservabilityAdapter()
        # Should not raise
        adapter.shutdown()


class TestObservabilityPortContract:
    """Verify ObservabilityPort interface contract."""

    def test_span_handle_is_abstract(self) -> None:
        """SpanHandle cannot be instantiated directly."""
        with pytest.raises(TypeError):
            SpanHandle()  # type: ignore

    def test_observability_port_is_abstract(self) -> None:
        """ObservabilityPort cannot be instantiated directly."""
        with pytest.raises(TypeError):
            ObservabilityPort()  # type: ignore
