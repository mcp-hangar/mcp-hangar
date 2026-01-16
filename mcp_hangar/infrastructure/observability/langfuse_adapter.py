"""Langfuse adapter for MCP Hangar observability.

This module provides thread-safe integration with Langfuse for tracing
MCP tool invocations, recording health scores, and correlating traces
with external LLM applications.

Example:
    config = LangfuseConfig(
        public_key="pk-...",
        secret_key="sk-...",
    )
    adapter = LangfuseObservabilityAdapter(config)

    span = adapter.start_tool_span("math", "add", {"a": 1, "b": 2})
    try:
        result = invoke_tool(...)
        span.end_success(result)
    except Exception as e:
        span.end_error(e)

Note:
    Requires `langfuse` package. Install with:
    pip install mcp-hangar[observability]
"""

from dataclasses import dataclass
import logging
import threading
import time
from typing import Any
import uuid

from ...application.ports.observability import ObservabilityPort, SpanHandle, TraceContext

logger = logging.getLogger(__name__)

# Lazy import to handle optional dependency
_langfuse_available = False
_Langfuse: type | None = None

try:
    from langfuse import Langfuse as _LangfuseClient

    _langfuse_available = True
    _Langfuse = _LangfuseClient
except ImportError:
    _LangfuseClient = None  # type: ignore[misc, assignment]
    logger.debug("Langfuse not installed. Install with: pip install mcp-hangar[observability]")


@dataclass(frozen=True)
class LangfuseConfig:
    """Configuration for Langfuse integration.

    Attributes:
        enabled: Whether Langfuse tracing is active.
        public_key: Langfuse public API key.
        secret_key: Langfuse secret API key.
        host: Langfuse host URL.
        flush_interval_s: Interval for background flushes.
        sample_rate: Fraction of traces to sample (0.0 to 1.0).
        scrub_inputs: Whether to redact sensitive input data.
        scrub_outputs: Whether to redact sensitive output data.
    """

    enabled: bool = True
    public_key: str = ""
    secret_key: str = ""
    host: str = "https://cloud.langfuse.com"
    flush_interval_s: float = 1.0
    sample_rate: float = 1.0
    scrub_inputs: bool = False
    scrub_outputs: bool = False

    def validate(self) -> list[str]:
        """Validate configuration, return list of errors."""
        errors = []
        if self.enabled:
            if not self.public_key:
                errors.append("langfuse.public_key is required when enabled")
            if not self.secret_key:
                errors.append("langfuse.secret_key is required when enabled")
            if not 0.0 <= self.sample_rate <= 1.0:
                errors.append("langfuse.sample_rate must be between 0.0 and 1.0")
        return errors


class LangfuseAdapter:
    """Low-level thread-safe wrapper around Langfuse SDK.

    This adapter handles SDK initialization, connection management,
    and provides thread-safe access to Langfuse operations.

    Compatible with Langfuse SDK v3.x API.
    """

    def __init__(self, config: LangfuseConfig) -> None:
        """Initialize Langfuse adapter.

        Args:
            config: Langfuse configuration.

        Raises:
            ImportError: If langfuse package is not installed.
            ValueError: If configuration is invalid.
        """
        self._config = config
        self._lock = threading.Lock()
        self._client: Any = None

        if not config.enabled:
            logger.info("Langfuse integration disabled by configuration")
            return

        if not _langfuse_available:
            raise ImportError("Langfuse package not installed. Install with: pip install mcp-hangar[observability]")

        errors = config.validate()
        if errors:
            raise ValueError(f"Invalid Langfuse config: {'; '.join(errors)}")

        self._client = _Langfuse(
            public_key=config.public_key,
            secret_key=config.secret_key,
            host=config.host,
        )
        logger.info("Langfuse adapter initialized", extra={"host": config.host})

    @property
    def is_enabled(self) -> bool:
        """Check if Langfuse is enabled and initialized."""
        return self._config.enabled and self._client is not None

    def start_span(
        self,
        name: str,
        trace_id: str | None = None,
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> tuple[Any, str]:
        """Start a new span (creates trace automatically in v3 API).

        Args:
            name: Span name.
            trace_id: Optional trace ID for correlation.
            input_data: Input data for the span.
            metadata: Optional metadata.
            user_id: Optional user ID for attribution.
            session_id: Optional session ID for grouping.

        Returns:
            Tuple of (Langfuse span object, trace_id) or (None, "") if disabled.
        """
        if not self.is_enabled:
            return None, ""

        input_to_send = input_data
        if self._config.scrub_inputs and input_data:
            input_to_send = {"scrubbed": True, "keys": list(input_data.keys())}

        # Generate or use provided trace_id
        # Langfuse v3 requires 32 lowercase hex char trace id
        if trace_id:
            # Convert UUID format to hex if needed
            effective_trace_id = trace_id.replace("-", "").lower()[:32]
            if len(effective_trace_id) < 32:
                effective_trace_id = effective_trace_id.ljust(32, "0")
        else:
            effective_trace_id = uuid.uuid4().hex

        # Build trace_context for Langfuse v3 API
        try:
            from langfuse.types import TraceContext as LangfuseTraceContext

            trace_context = LangfuseTraceContext(
                trace_id=effective_trace_id,
                user_id=user_id,
                session_id=session_id,
            )
        except ImportError:
            trace_context = None

        with self._lock:
            span = self._client.start_span(
                name=name,
                trace_context=trace_context,
                input=input_to_send,
                metadata=metadata or {},
            )
            return span, effective_trace_id

    def end_span(
        self,
        span: Any,
        output: Any = None,
        level: str = "DEFAULT",
        status_message: str | None = None,
    ) -> None:
        """End a span with output.

        Args:
            span: Span object to end.
            output: Output data.
            level: Log level (DEFAULT, DEBUG, WARNING, ERROR).
            status_message: Optional status message.
        """
        if not self.is_enabled or span is None:
            return

        output_to_send = output
        if self._config.scrub_outputs and output is not None:
            if isinstance(output, dict):
                output_to_send = {"scrubbed": True, "keys": list(output.keys())}
            else:
                output_to_send = {"scrubbed": True, "type": type(output).__name__}

        with self._lock:
            # Update span with output before ending
            span.update(
                output=output_to_send,
                level=level,
                status_message=status_message,
            )
            span.end()

    def create_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        """Record a score on a trace.

        Args:
            trace_id: Trace ID.
            name: Score name.
            value: Score value.
            comment: Optional comment.
        """
        if not self.is_enabled:
            return

        with self._lock:
            self._client.create_score(
                trace_id=trace_id,
                name=name,
                value=value,
                comment=comment,
            )

    def flush(self) -> None:
        """Flush pending events to Langfuse."""
        if not self.is_enabled:
            return

        with self._lock:
            self._client.flush()

    def shutdown(self) -> None:
        """Shutdown with final flush."""
        if not self.is_enabled:
            return

        logger.info("Shutting down Langfuse adapter")
        with self._lock:
            self._client.shutdown()


class LangfuseSpanHandle(SpanHandle):
    """Span handle implementation for Langfuse.

    Tracks timing and manages span lifecycle with proper
    success/error handling.
    """

    def __init__(
        self,
        adapter: LangfuseAdapter,
        span: Any,
        trace_id: str,
    ) -> None:
        """Initialize span handle.

        Args:
            adapter: Langfuse adapter instance.
            span: Span object.
            trace_id: Trace ID for scoring.
        """
        self._adapter = adapter
        self._span = span
        self._trace_id = trace_id
        self._start_time = time.perf_counter()
        self._ended = False

    def end_success(self, output: Any) -> None:
        """End span with successful outcome.

        Args:
            output: Result data.
        """
        if self._ended:
            logger.warning("Span already ended, ignoring duplicate end_success call")
            return

        self._ended = True
        duration_ms = (time.perf_counter() - self._start_time) * 1000

        self._adapter.end_span(
            self._span,
            output=output,
            level="DEFAULT",
        )

        # Record latency as score
        self._adapter.create_score(
            trace_id=self._trace_id,
            name="tool_latency_ms",
            value=duration_ms,
        )

    def end_error(self, error: Exception) -> None:
        """End span with error.

        Args:
            error: The exception that occurred.
        """
        if self._ended:
            logger.warning("Span already ended, ignoring duplicate end_error call")
            return

        self._ended = True
        duration_ms = (time.perf_counter() - self._start_time) * 1000

        self._adapter.end_span(
            self._span,
            output={"error": str(error), "type": type(error).__name__},
            level="ERROR",
            status_message=f"Tool invocation failed: {error}",
        )

        # Record failure score
        self._adapter.create_score(
            trace_id=self._trace_id,
            name="tool_success",
            value=0.0,
            comment=str(error),
        )

        self._adapter.create_score(
            trace_id=self._trace_id,
            name="tool_latency_ms",
            value=duration_ms,
        )

    def set_metadata(self, key: str, value: Any) -> None:
        """Add metadata to span.

        Note: Langfuse spans don't support updating metadata after creation.
        This logs a warning and stores for debugging.

        Args:
            key: Metadata key.
            value: Metadata value.
        """
        logger.debug(
            "Span metadata update requested (not supported by Langfuse)",
            extra={"key": key, "value": value},
        )


class LangfuseObservabilityAdapter(ObservabilityPort):
    """Observability port implementation using Langfuse.

    Provides full tracing and scoring capabilities for MCP tool
    invocations, integrating with the Langfuse observability platform.
    """

    def __init__(self, config: LangfuseConfig) -> None:
        """Initialize adapter.

        Args:
            config: Langfuse configuration.
        """
        self._config = config
        self._adapter = LangfuseAdapter(config)
        self._sample_lock = threading.Lock()

    def _should_sample(self) -> bool:
        """Determine if this trace should be sampled."""
        if self._config.sample_rate >= 1.0:
            return True
        if self._config.sample_rate <= 0.0:
            return False

        import random

        with self._sample_lock:
            return random.random() < self._config.sample_rate

    def start_tool_span(
        self,
        provider_name: str,
        tool_name: str,
        input_params: dict[str, Any],
        trace_context: TraceContext | None = None,
    ) -> SpanHandle:
        """Start a traced span for tool invocation.

        Args:
            provider_name: Provider name.
            tool_name: Tool name.
            input_params: Tool input parameters.
            trace_context: Optional trace context for correlation.

        Returns:
            Span handle for managing the span lifecycle.
        """
        if not self._adapter.is_enabled or not self._should_sample():
            from ...application.ports.observability import NullSpanHandle

            return NullSpanHandle()

        span, trace_id = self._adapter.start_span(
            name=f"mcp/{provider_name}/{tool_name}",
            trace_id=trace_context.trace_id if trace_context else None,
            input_data=input_params,
            metadata={
                "provider": provider_name,
                "tool": tool_name,
                "mcp_hangar": True,
            },
            user_id=trace_context.user_id if trace_context else None,
            session_id=trace_context.session_id if trace_context else None,
        )

        # Record success score at start (will be updated on error)
        self._adapter.create_score(
            trace_id=trace_id,
            name="tool_success",
            value=1.0,
        )

        return LangfuseSpanHandle(
            adapter=self._adapter,
            span=span,
            trace_id=trace_id,
        )

    def record_score(
        self,
        trace_id: str,
        name: str,
        value: float,
        comment: str | None = None,
    ) -> None:
        """Record a score on a trace.

        Args:
            trace_id: Trace ID.
            name: Score name.
            value: Score value.
            comment: Optional comment.
        """
        self._adapter.create_score(
            trace_id=trace_id,
            name=name,
            value=value,
            comment=comment,
        )

    def record_health_check(
        self,
        provider_name: str,
        healthy: bool,
        latency_ms: float,
        trace_id: str | None = None,
    ) -> None:
        """Record provider health check result.

        If no trace_id is provided, creates a standalone span for the
        health check event.

        Args:
            provider_name: Provider name.
            healthy: Whether the check passed.
            latency_ms: Check latency in milliseconds.
            trace_id: Optional trace to attach to.
        """
        if not self._adapter.is_enabled:
            return

        effective_trace_id = trace_id

        if trace_id is None:
            # Create standalone health check span
            span, effective_trace_id = self._adapter.start_span(
                name=f"health/{provider_name}",
                metadata={
                    "provider": provider_name,
                    "type": "health_check",
                },
            )
            if span:
                self._adapter.end_span(
                    span,
                    output={"healthy": healthy, "latency_ms": latency_ms},
                    level="DEFAULT" if healthy else "WARNING",
                )
        else:
            effective_trace_id = trace_id

        self._adapter.create_score(
            trace_id=effective_trace_id,
            name="provider_healthy",
            value=1.0 if healthy else 0.0,
            comment=f"Provider: {provider_name}",
        )

        self._adapter.create_score(
            trace_id=effective_trace_id,
            name="health_check_latency_ms",
            value=latency_ms,
        )

    def flush(self) -> None:
        """Flush pending events."""
        self._adapter.flush()

    def shutdown(self) -> None:
        """Shutdown with final flush."""
        self._adapter.shutdown()
