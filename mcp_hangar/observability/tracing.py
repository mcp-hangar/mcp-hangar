"""OpenTelemetry tracing for MCP Hangar.

Provides distributed tracing with automatic context propagation
through tool invocations and provider calls.

Configuration via environment variables:
    OTEL_EXPORTER_OTLP_ENDPOINT: OTLP endpoint (default: http://localhost:4317)
    OTEL_SERVICE_NAME: Service name (default: mcp-hangar)
    OTEL_TRACES_SAMPLER: Sampler type (default: always_on)
    MCP_TRACING_ENABLED: Enable/disable tracing (default: true)

Example:
    from mcp_hangar.observability.tracing import init_tracing, get_tracer

    # Initialize once at startup
    init_tracing()

    # Get tracer for module
    tracer = get_tracer(__name__)

    # Create spans
    with tracer.start_as_current_span("my_operation") as span:
        span.set_attribute("key", "value")
        do_work()
"""

from contextlib import contextmanager
from functools import wraps
import os
from typing import Any, Callable, Dict, Optional, TypeVar

from mcp_hangar.logging_config import get_logger

logger = get_logger(__name__)

# Type variable for generic decorator
F = TypeVar("F", bound=Callable[..., Any])

# Global state
_tracer_provider = None
_initialized = False

# Check if OpenTelemetry is available
try:
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry import trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace import Span, Status, StatusCode, Tracer

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None
    Tracer = None
    Span = None

# Try to import OTLP exporter
try:
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

    OTLP_AVAILABLE = True
except ImportError:
    OTLP_AVAILABLE = False
    OTLPSpanExporter = None

# Try to import Jaeger exporter
try:
    from opentelemetry.exporter.jaeger.thrift import JaegerExporter

    JAEGER_AVAILABLE = True
except ImportError:
    JAEGER_AVAILABLE = False
    JaegerExporter = None


class NoOpSpan:
    """No-op span for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def add_event(self, name: str, attributes: Optional[Dict] = None) -> None:
        pass

    def __enter__(self) -> "NoOpSpan":
        return self

    def __exit__(self, *args) -> None:
        pass


class NoOpTracer:
    """No-op tracer for when tracing is disabled."""

    def start_as_current_span(self, name: str, **kwargs) -> NoOpSpan:
        return NoOpSpan()

    @contextmanager
    def start_span(self, name: str, **kwargs):
        yield NoOpSpan()


_noop_tracer = NoOpTracer()


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled."""
    enabled = os.getenv("MCP_TRACING_ENABLED", "true").lower()
    return enabled in ("true", "1", "yes") and OTEL_AVAILABLE


def init_tracing(
    service_name: str = "mcp-hangar",
    otlp_endpoint: Optional[str] = None,
    jaeger_host: Optional[str] = None,
    jaeger_port: int = 6831,
    console_export: bool = False,
) -> bool:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Service name for traces.
        otlp_endpoint: OTLP collector endpoint (gRPC).
        jaeger_host: Jaeger agent host for UDP export.
        jaeger_port: Jaeger agent port.
        console_export: Enable console span export (for debugging).

    Returns:
        True if tracing was initialized, False otherwise.
    """
    global _tracer_provider, _initialized

    if _initialized:
        logger.debug("tracing_already_initialized")
        return True

    if not OTEL_AVAILABLE:
        logger.info(
            "tracing_disabled_otel_not_available",
            hint="Install opentelemetry-api and opentelemetry-sdk",
        )
        return False

    if not is_tracing_enabled():
        logger.info("tracing_disabled_by_config")
        return False

    try:
        # Get endpoint from env or parameter
        otlp_endpoint = otlp_endpoint or os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4317")

        # Create resource with service info
        resource = Resource.create(
            {
                SERVICE_NAME: service_name,
                "service.version": _get_version(),
                "deployment.environment": os.getenv("MCP_ENVIRONMENT", "development"),
            }
        )

        # Create tracer provider
        _tracer_provider = TracerProvider(resource=resource)

        # Add exporters
        exporters_added = 0

        # OTLP exporter (preferred)
        if OTLP_AVAILABLE and otlp_endpoint:
            try:
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
                _tracer_provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
                exporters_added += 1
                logger.info("tracing_otlp_exporter_added", endpoint=otlp_endpoint)
            except Exception as e:
                logger.warning("tracing_otlp_exporter_failed", error=str(e))

        # Jaeger exporter (fallback)
        if JAEGER_AVAILABLE and jaeger_host:
            try:
                jaeger_exporter = JaegerExporter(
                    agent_host_name=jaeger_host,
                    agent_port=jaeger_port,
                )
                _tracer_provider.add_span_processor(BatchSpanProcessor(jaeger_exporter))
                exporters_added += 1
                logger.info(
                    "tracing_jaeger_exporter_added",
                    host=jaeger_host,
                    port=jaeger_port,
                )
            except Exception as e:
                logger.warning("tracing_jaeger_exporter_failed", error=str(e))

        # Console exporter (debugging)
        if console_export:
            console_exporter = ConsoleSpanExporter()
            _tracer_provider.add_span_processor(BatchSpanProcessor(console_exporter))
            exporters_added += 1
            logger.info("tracing_console_exporter_added")

        if exporters_added == 0:
            logger.warning("tracing_no_exporters_configured")
            return False

        # Set global tracer provider
        trace.set_tracer_provider(_tracer_provider)
        _initialized = True

        logger.info(
            "tracing_initialized",
            service_name=service_name,
            exporters=exporters_added,
        )
        return True

    except Exception as e:
        logger.error("tracing_initialization_failed", error=str(e))
        return False


def shutdown_tracing() -> None:
    """Shutdown tracing and flush pending spans."""
    global _tracer_provider, _initialized

    if _tracer_provider is not None:
        try:
            _tracer_provider.shutdown()
            logger.info("tracing_shutdown_complete")
        except Exception as e:
            logger.warning("tracing_shutdown_error", error=str(e))
        finally:
            _tracer_provider = None
            _initialized = False


def get_tracer(name: str = __name__) -> Any:
    """Get a tracer instance.

    Args:
        name: Tracer name (usually __name__).

    Returns:
        OpenTelemetry tracer or NoOpTracer if disabled.
    """
    if not _initialized or not OTEL_AVAILABLE:
        return _noop_tracer

    return trace.get_tracer(name)


def trace_tool_invocation(
    provider_id: str,
    tool_name: str,
    timeout: float,
) -> Callable[[F], F]:
    """Decorator to trace tool invocations.

    Args:
        provider_id: Provider ID.
        tool_name: Tool name.
        timeout: Timeout in seconds.

    Example:
        @trace_tool_invocation("sqlite", "query", 30.0)
        def invoke_tool(...):
            ...
    """

    def decorator(func: F) -> F:
        @wraps(func)
        def wrapper(*args, **kwargs):
            tracer = get_tracer(__name__)

            with tracer.start_as_current_span(
                f"tool.invoke.{tool_name}",
                kind=trace.SpanKind.CLIENT if OTEL_AVAILABLE else None,
            ) as span:
                # Set standard attributes
                span.set_attribute("mcp.provider.id", provider_id)
                span.set_attribute("mcp.tool.name", tool_name)
                span.set_attribute("mcp.timeout_seconds", timeout)

                try:
                    result = func(*args, **kwargs)
                    span.set_attribute("mcp.result.success", True)
                    return result
                except Exception as e:
                    span.set_attribute("mcp.result.success", False)
                    span.set_attribute("mcp.error.type", type(e).__name__)
                    span.set_attribute("mcp.error.message", str(e)[:500])
                    span.record_exception(e)
                    if OTEL_AVAILABLE:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return wrapper

    return decorator


@contextmanager
def trace_span(
    name: str,
    attributes: Optional[Dict[str, Any]] = None,
    kind: Optional[str] = None,
):
    """Context manager for creating trace spans.

    Args:
        name: Span name.
        attributes: Initial span attributes.
        kind: Span kind (client, server, producer, consumer, internal).

    Example:
        with trace_span("my_operation", {"key": "value"}) as span:
            span.add_event("checkpoint_reached")
            do_work()
    """
    tracer = get_tracer(__name__)

    span_kind = None
    if OTEL_AVAILABLE and kind:
        kind_map = {
            "client": trace.SpanKind.CLIENT,
            "server": trace.SpanKind.SERVER,
            "producer": trace.SpanKind.PRODUCER,
            "consumer": trace.SpanKind.CONSUMER,
            "internal": trace.SpanKind.INTERNAL,
        }
        span_kind = kind_map.get(kind.lower())

    with tracer.start_as_current_span(name, kind=span_kind) as span:
        if attributes:
            for key, value in attributes.items():
                span.set_attribute(key, value)
        yield span


def inject_trace_context(carrier: Dict[str, str]) -> None:
    """Inject trace context into carrier dict for propagation.

    Args:
        carrier: Dict to inject trace context into.

    Example:
        headers = {}
        inject_trace_context(headers)
        # headers now contains traceparent, tracestate
    """
    if not OTEL_AVAILABLE or not _initialized:
        return

    propagator = TraceContextTextMapPropagator()
    propagator.inject(carrier)


def extract_trace_context(carrier: Dict[str, str]) -> Any:
    """Extract trace context from carrier dict.

    Args:
        carrier: Dict containing trace context.

    Returns:
        OpenTelemetry context or None.

    Example:
        context = extract_trace_context(request.headers)
        with tracer.start_as_current_span("handle", context=context):
            ...
    """
    if not OTEL_AVAILABLE or not _initialized:
        return None

    propagator = TraceContextTextMapPropagator()
    return propagator.extract(carrier)


def get_current_trace_id() -> Optional[str]:
    """Get current trace ID as hex string.

    Returns:
        Trace ID or None if not in a trace.
    """
    if not OTEL_AVAILABLE or not _initialized:
        return None

    span = trace.get_current_span()
    if span is None:
        return None

    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None

    return format(ctx.trace_id, "032x")


def get_current_span_id() -> Optional[str]:
    """Get current span ID as hex string.

    Returns:
        Span ID or None if not in a span.
    """
    if not OTEL_AVAILABLE or not _initialized:
        return None

    span = trace.get_current_span()
    if span is None:
        return None

    ctx = span.get_span_context()
    if ctx is None or not ctx.is_valid:
        return None

    return format(ctx.span_id, "016x")


def _get_version() -> str:
    """Get MCP Hangar version."""
    try:
        from mcp_hangar import __version__

        return __version__
    except (ImportError, AttributeError):
        return "unknown"
