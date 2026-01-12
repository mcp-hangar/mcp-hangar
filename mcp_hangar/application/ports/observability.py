"""Port for observability integrations (Langfuse, OpenTelemetry, etc.).

This module defines the interface for tracing tool invocations and recording
metrics. Implementations adapt external observability platforms to this contract.

Example usage:
    observability = get_observability_adapter(config)
    span = observability.start_tool_span("math", "add", {"a": 1, "b": 2})
    try:
        result = provider.invoke(...)
        span.end_success(result)
    except Exception as e:
        span.end_error(e)
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
import logging
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TraceContext:
    """Value object for trace context propagation across MCP calls.

    Enables correlation of traces from external LLM applications
    through MCP Hangar to individual provider invocations.

    Attributes:
        trace_id: Unique identifier for the trace.
        span_id: Identifier for the parent span (optional).
        user_id: User identifier for attribution (optional).
        session_id: Session identifier for grouping (optional).
    """

    trace_id: str
    span_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None


@dataclass
class SpanData:
    """Data collected during a traced span.

    Attributes:
        name: Human-readable span name.
        input_data: Input parameters for the operation.
        output_data: Result of the operation (set on completion).
        metadata: Additional context for the span.
        error: Error if the span ended with failure.
        duration_ms: Duration of the span in milliseconds.
    """

    name: str
    input_data: dict[str, Any] = field(default_factory=dict)
    output_data: Any = None
    metadata: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    duration_ms: float | None = None


class SpanHandle(ABC):
    """Handle for an active observability span.

    Implementations should capture timing and allow setting
    success/failure status with output data.
    """

    @abstractmethod
    def end_success(self, output: Any) -> None:
        """End the span with a successful outcome.

        Args:
            output: The result data from the traced operation.
        """
        ...

    @abstractmethod
    def end_error(self, error: Exception) -> None:
        """End the span with an error.

        Args:
            error: The exception that caused the failure.
        """
        ...

    @abstractmethod
    def set_metadata(self, key: str, value: Any) -> None:
        """Add metadata to the span.

        Args:
            key: Metadata key.
            value: Metadata value (must be JSON-serializable).
        """
        ...


class ObservabilityPort(ABC):
    """Port interface for observability integrations.

    Implementations adapt external platforms (Langfuse, OpenTelemetry, etc.)
    to provide tracing and scoring capabilities for MCP tool invocations.
    """

    @abstractmethod
    def start_tool_span(
        self,
        provider_name: str,
        tool_name: str,
        input_params: dict[str, Any],
        trace_context: TraceContext | None = None,
    ) -> SpanHandle:
        """Start a traced span for a tool invocation.

        Args:
            provider_name: Name of the MCP provider.
            tool_name: Name of the tool being invoked.
            input_params: Input arguments for the tool.
            trace_context: Optional context for trace propagation.

        Returns:
            Handle to manage the span lifecycle.
        """
        ...

    @abstractmethod
    def record_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        """Record a score/metric on a trace.

        Useful for recording provider health, latency, or quality metrics.

        Args:
            trace_id: The trace to attach the score to.
            name: Score name (e.g., "provider_health", "latency_ms").
            value: Numeric score value.
            comment: Optional description.
        """
        ...

    @abstractmethod
    def record_health_check(
        self,
        provider_name: str,
        healthy: bool,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Record a health check result.

        Args:
            provider_name: Name of the provider.
            healthy: Whether the health check passed.
            latency_ms: Health check latency in milliseconds.
            trace_id: Optional trace to attach the result to.
        """
        ...

    @abstractmethod
    def flush(self) -> None:
        """Flush any pending events to the backend."""
        ...

    @abstractmethod
    def shutdown(self) -> None:
        """Gracefully shutdown with final flush."""
        ...


class NullSpanHandle(SpanHandle):
    """No-op span handle when observability is disabled."""

    def end_success(self, output: Any) -> None:
        """No-op success."""
        pass

    def end_error(self, error: Exception) -> None:
        """No-op error."""
        pass

    def set_metadata(self, key: str, value: Any) -> None:
        """No-op metadata."""
        pass


class NullObservabilityAdapter(ObservabilityPort):
    """No-op implementation when observability is disabled.

    This adapter silently discards all tracing and scoring calls,
    allowing the application to run without any observability overhead.
    """

    def start_tool_span(
        self,
        provider_name: str,
        tool_name: str,
        input_params: dict[str, Any],
        trace_context: TraceContext | None = None,
    ) -> SpanHandle:
        """Return a no-op span handle."""
        return NullSpanHandle()

    def record_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        """Discard the score."""
        pass

    def record_health_check(
        self,
        provider_name: str,
        healthy: bool,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Discard the health check result."""
        pass

    def flush(self) -> None:
        """No-op flush."""
        pass

    def shutdown(self) -> None:
        """No-op shutdown."""
        pass
