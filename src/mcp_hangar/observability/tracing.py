"""OpenTelemetry tracing for MCP Hangar.

Provides distributed tracing with automatic context propagation
through tool invocations and mcp_server calls.

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

from collections.abc import Callable
from contextlib import contextmanager
from functools import wraps
import os
from typing import Any, TypeVar

from mcp_hangar.logging_config import get_logger
from mcp_hangar.metrics import record_otlp_export_failure
from mcp_hangar.observability.conventions import GenAI, MCP, McpServer

logger = get_logger(__name__)

# Type variable for generic decorator
F = TypeVar("F", bound=Callable[..., Any])

# Global state
_tracer_mcp_server = None
_initialized = False

# Check if OpenTelemetry is available
try:
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    from opentelemetry.sdk.trace.export import (
        BatchSpanProcessor,
        ConsoleSpanExporter,
        SpanExporter,
        SpanExportResult,
    )
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.sampling import (
        ALWAYS_OFF,
        ALWAYS_ON,
        ParentBased,
        TraceIdRatioBased,
    )
    from opentelemetry import trace
    from opentelemetry.baggage.propagation import W3CBaggagePropagator
    from opentelemetry.propagators.composite import CompositePropagator
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
    from opentelemetry.trace import Status, StatusCode

    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False
    trace = None

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


if OTEL_AVAILABLE:

    class _MeteredSpanExporter(SpanExporter):
        """Wrap a SpanExporter to make export failures observable.

        ``BatchSpanProcessor`` calls ``export()`` on a background thread and
        swallows failures, so an unreachable collector is silent and the spans
        buffered in that batch are dropped without a metric. This decorator
        increments ``mcp_hangar_otlp_export_failures_total`` when the wrapped
        exporter returns a failure result or raises. It never changes export
        semantics: the inner result (or exception) is propagated unchanged, so
        the SDK's own retry/backoff behaviour is preserved and the MCP path is
        never blocked.
        """

        def __init__(self, inner: SpanExporter) -> None:
            self._inner = inner

        def export(self, spans: Any) -> "SpanExportResult":
            try:
                result = self._inner.export(spans)
            except Exception:
                record_otlp_export_failure()
                raise
            if result is not SpanExportResult.SUCCESS:
                record_otlp_export_failure()
            return result

        def shutdown(self) -> None:
            self._inner.shutdown()

        def force_flush(self, timeout_millis: int = 30000) -> bool:
            return bool(self._inner.force_flush(timeout_millis))


class NoOpSpan:
    """No-op span for when tracing is disabled."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def set_status(self, status: Any) -> None:
        pass

    def record_exception(self, exception: Exception) -> None:
        pass

    def add_event(self, name: str, attributes: dict | None = None) -> None:
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

# W3C baggage handling (SEP-414).
#
# Baggage is a set of opaque, application-defined key/value pairs propagated
# alongside trace context. Unlike traceparent/tracestate (which are safe,
# structural trace identifiers), baggage can carry arbitrary caller-supplied
# data and therefore MUST NOT be allowed to leak across a tenant boundary.
#
# Hangar owns a dedicated baggage namespace: entries whose keys start with
# ``HANGAR_BAGGAGE_PREFIX`` are considered "Hangar-set" (trusted, produced by
# Hangar itself in the current request context). Everything else is treated as
# untrusted / cross-origin baggage and is dropped on the outbound path.
BAGGAGE_HEADER = "baggage"
HANGAR_BAGGAGE_PREFIX = "hangar."
# Optional tenant marker Hangar may set to bind baggage to a single tenant.
HANGAR_BAGGAGE_TENANT_KEY = "hangar.tenant"


def _get_propagator() -> Any:
    """Build the composite propagator used for inbound/outbound context.

    Composes the existing W3C TraceContext propagator (traceparent/tracestate)
    with the W3C Baggage propagator so that ``baggage`` is extracted from
    inbound carriers (making it available in-context) and injected onto
    outbound carriers. Trace-context behavior is unchanged: it is still the
    first propagator in the composite and continues to handle traceparent /
    tracestate exactly as before.
    """
    return CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()])


def is_tracing_enabled() -> bool:
    """Check if tracing is enabled."""
    enabled = os.getenv("MCP_TRACING_ENABLED", "true").lower()
    return enabled in ("true", "1", "yes") and OTEL_AVAILABLE


def _build_sampler() -> Any:
    """Build a sampler from OTEL_TRACES_SAMPLER / OTEL_TRACES_SAMPLER_ARG.

    We construct the TracerProvider by hand, so the SDK's standard env-var
    auto-configuration for sampling never runs. This mirrors that contract so
    OTEL_TRACES_SAMPLER actually takes effect (the docstring long claimed
    support that was never wired). Defaults to parentbased_always_on, matching
    the SDK default. For ratio samplers, OTEL_TRACES_SAMPLER_ARG is the ratio
    in [0, 1]; a missing/invalid arg falls back to 1.0 (sample everything).
    """
    name = os.getenv("OTEL_TRACES_SAMPLER", "parentbased_always_on").strip().lower()
    arg = os.getenv("OTEL_TRACES_SAMPLER_ARG", "")

    def _ratio(default: float) -> float:
        try:
            return float(arg)
        except (TypeError, ValueError):
            return default

    if name == "always_on":
        return ALWAYS_ON
    if name == "always_off":
        return ALWAYS_OFF
    if name == "traceidratio":
        return TraceIdRatioBased(_ratio(1.0))
    if name == "parentbased_always_off":
        return ParentBased(ALWAYS_OFF)
    if name == "parentbased_traceidratio":
        return ParentBased(TraceIdRatioBased(_ratio(1.0)))
    if name != "parentbased_always_on":
        logger.warning("tracing_unknown_sampler", sampler=name, fallback="parentbased_always_on")
    return ParentBased(ALWAYS_ON)


def init_tracing(
    service_name: str = "mcp-hangar",
    otlp_endpoint: str | None = None,
    jaeger_host: str | None = None,
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
    global _tracer_mcp_server, _initialized

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

        # Create tracer mcp_server
        sampler = _build_sampler()
        _tracer_mcp_server = TracerProvider(resource=resource, sampler=sampler)
        logger.info("tracing_sampler_configured", sampler=type(sampler).__name__)

        # Add exporters
        exporters_added = 0

        # OTLP exporter (preferred)
        if OTLP_AVAILABLE and otlp_endpoint:
            try:
                otlp_exporter = OTLPSpanExporter(endpoint=otlp_endpoint, insecure=True)
                _tracer_mcp_server.add_span_processor(BatchSpanProcessor(_MeteredSpanExporter(otlp_exporter)))
                exporters_added += 1
                logger.info("tracing_otlp_exporter_added", endpoint=otlp_endpoint)
            except Exception as e:  # noqa: BLE001 -- fault-barrier: exporter init must not crash tracing setup
                logger.warning("tracing_otlp_exporter_failed", error=str(e))

        # Jaeger exporter (fallback)
        if JAEGER_AVAILABLE and jaeger_host:
            try:
                jaeger_exporter = JaegerExporter(
                    agent_host_name=jaeger_host,
                    agent_port=jaeger_port,
                )
                _tracer_mcp_server.add_span_processor(BatchSpanProcessor(jaeger_exporter))
                exporters_added += 1
                logger.info(
                    "tracing_jaeger_exporter_added",
                    host=jaeger_host,
                    port=jaeger_port,
                )
            except Exception as e:  # noqa: BLE001 -- fault-barrier: exporter init must not crash tracing setup
                logger.warning("tracing_jaeger_exporter_failed", error=str(e))

        # Console exporter (debugging)
        if console_export:
            console_exporter = ConsoleSpanExporter()
            _tracer_mcp_server.add_span_processor(BatchSpanProcessor(console_exporter))
            exporters_added += 1
            logger.info("tracing_console_exporter_added")

        if exporters_added == 0:
            logger.warning("tracing_no_exporters_configured")
            return False

        # Register the global tracer provider (third-party OTel API)
        trace.set_tracer_provider(_tracer_mcp_server)
        _initialized = True

        logger.info(
            "tracing_initialized",
            service_name=service_name,
            exporters=exporters_added,
        )
        return True

    except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing init failure must not crash application
        logger.error("tracing_initialization_failed", error=str(e))
        return False


def shutdown_tracing() -> None:
    """Shutdown tracing and flush pending spans."""
    global _tracer_mcp_server, _initialized

    if _tracer_mcp_server is not None:
        try:
            _tracer_mcp_server.shutdown()
            logger.info("tracing_shutdown_complete")
        except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing shutdown must not crash application
            logger.warning("tracing_shutdown_error", error=str(e))
        finally:
            _tracer_mcp_server = None
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
    mcp_server_id: str,
    tool_name: str,
    timeout: float,
) -> Callable[[F], F]:
    """Decorator to trace tool invocations.

    Args:
        mcp_server_id: McpServer ID.
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
                f"execute_tool {tool_name}",
                kind=trace.SpanKind.CLIENT if OTEL_AVAILABLE else None,
            ) as span:
                # Set standard attributes
                span.set_attribute(McpServer.ID, mcp_server_id)
                span.set_attribute(GenAI.TOOL_NAME, tool_name)
                span.set_attribute(GenAI.OPERATION_NAME, "execute_tool")
                span.set_attribute(MCP.METHOD_NAME, "tools/call")
                span.set_attribute("mcp.timeout_seconds", timeout)

                try:
                    result = func(*args, **kwargs)
                    span.set_attribute(MCP.TOOL_STATUS, "success")
                    return result
                except Exception as e:  # noqa: BLE001 -- fault-barrier: tracing must not crash tool invocation; re-raises original
                    span.set_attribute(MCP.TOOL_STATUS, "error")
                    span.set_attribute("mcp.error.type", type(e).__name__)
                    span.set_attribute("mcp.error.message", str(e)[:500])
                    span.record_exception(e)
                    if OTEL_AVAILABLE:
                        span.set_status(Status(StatusCode.ERROR, str(e)))
                    raise

        return wrapper  # type: ignore[return-value]  # decorated wrapper preserves F signature via @wraps

    return decorator


@contextmanager
def trace_span(
    name: str,
    attributes: dict[str, Any] | None = None,
    kind: str | None = None,
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


@contextmanager
def upstream_call_span(method: str, params: dict[str, Any] | None = None):
    """CLIENT span for an outgoing MCP RPC to an upstream server.

    Wrap the transport call with this so the RPC is a proper SpanKind.CLIENT
    span and the trace context injected downstream (HTTP headers or stdio
    `_meta`) parents the upstream's server span to this one. Names/attributes
    follow OTel GenAI/MCP semconv. No-op when tracing is disabled.

    Args:
        method: JSON-RPC method (e.g. "tools/call", "tools/list", "initialize").
        params: Request params; for tools/call the tool name is read from
            ``params["name"]`` for the span name and gen_ai.tool.name.
    """
    params = params or {}
    attributes: dict[str, Any] = {MCP.METHOD_NAME: method}
    if method == "tools/call":
        tool = params.get("name")
        name = f"execute_tool {tool}" if tool else "execute_tool"
        attributes[GenAI.OPERATION_NAME] = "execute_tool"
        if tool:
            attributes[GenAI.TOOL_NAME] = tool
    else:
        name = method

    with trace_span(name, attributes=attributes, kind="client") as span:
        yield span


def mark_span_error(span: Any, description: str | None = None) -> None:
    """Set ERROR status on a span. Safe for NoOp spans and when OTel is absent.

    Use when a failure is handled as data (e.g. converted to a result object)
    rather than raised, so the span would otherwise stay UNSET and the failing
    operation would look successful in the trace UI.
    """
    if not OTEL_AVAILABLE:
        return
    try:
        span.set_status(Status(StatusCode.ERROR, description or ""))
    except Exception:  # noqa: BLE001 -- fault-barrier: tracing must not break the traced path
        pass


def inject_trace_context(carrier: dict[str, str]) -> None:
    """Inject trace context into carrier dict for propagation.

    Args:
        carrier: Dict to inject trace context into.

    Injects W3C TraceContext (traceparent/tracestate) and W3C ``baggage`` from
    the current OTel context. Trace-context behavior is unchanged. Baggage that
    happens to be present in the current context (for example, extracted from an
    inbound request) is injected too; callers on a tenant-crossing outbound path
    MUST additionally run :func:`scrub_baggage_for_tenant` to strip untrusted /
    cross-tenant baggage before sending.

    Example:
        headers = {}
        inject_trace_context(headers)
        # headers now contains traceparent, tracestate (and baggage, if any)
    """
    if not OTEL_AVAILABLE or not _initialized:
        return

    _get_propagator().inject(carrier)


def extract_trace_context(carrier: dict[str, str]) -> Any:
    """Extract trace context from carrier dict.

    Args:
        carrier: Dict containing trace context.

    Returns:
        OpenTelemetry context or None.

    Extracts W3C TraceContext (traceparent/tracestate) and W3C ``baggage`` from
    the carrier so both are available in the returned context. Trace-context
    behavior is unchanged.

    Example:
        context = extract_trace_context(request.headers)
        with tracer.start_as_current_span("handle", context=context):
            ...
    """
    if not OTEL_AVAILABLE or not _initialized:
        return None

    return _get_propagator().extract(carrier)


def scrub_baggage_for_tenant(carrier: dict[str, str], current_tenant_id: str | None) -> None:
    """Strip cross-tenant / untrusted W3C ``baggage`` from an OUTBOUND carrier.

    Baggage keys and values are opaque and application-defined, so Hangar cannot
    reliably reason about the tenant-safety of arbitrary entries it did not
    create. This function is therefore **conservative by default**: it drops any
    baggage that is not attributable to Hangar in the current single-tenant
    request context.

    The rule (fail-safe against cross-tenant leak):

    * Keep only entries in the Hangar-owned namespace (keys starting with
      ``HANGAR_BAGGAGE_PREFIX``). These are entries Hangar itself set in the
      current request context; inbound-originated / third-party baggage is
      dropped.
    * If a Hangar tenant marker (``HANGAR_BAGGAGE_TENANT_KEY``) is present and
      its value does not match ``current_tenant_id``, treat the whole carrier as
      cross-tenant and drop **all** baggage. A ``current_tenant_id`` of ``None``
      means "tenant unknown", which is also treated as a mismatch — when we
      cannot prove same-tenant, we do not forward.

    The ``baggage`` carrier entry is removed entirely when nothing survives.

    This mechanism is intentionally strict and refinable later (for example, an
    allowlist of forwardable inbound keys once their provenance can be trusted).
    It operates purely on the carrier string and does not depend on OTEL being
    installed, so it remains a hard boundary even when tracing is disabled.

    Args:
        carrier: Outbound carrier dict (e.g. HTTP headers or MCP ``_meta``)
            that may contain a ``baggage`` entry. Mutated in place.
        current_tenant_id: The tenant the outbound request is attributed to, or
            ``None`` if unknown.
    """
    raw = carrier.get(BAGGAGE_HEADER)
    if not raw:
        return

    kept: list[str] = []
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        # A baggage member is "key=value" optionally followed by ";properties".
        key_part, value_part = item.split("=", 1)
        key = key_part.strip()

        if not key.startswith(HANGAR_BAGGAGE_PREFIX):
            # Untrusted / inbound-originated / cross-origin baggage: drop.
            continue

        if key == HANGAR_BAGGAGE_TENANT_KEY:
            value = value_part.split(";", 1)[0].strip()
            if current_tenant_id is None or value != current_tenant_id:
                # Explicit cross-tenant signal: drop ALL baggage, not just this entry.
                carrier.pop(BAGGAGE_HEADER, None)
                return

        kept.append(item)

    if kept:
        carrier[BAGGAGE_HEADER] = ",".join(kept)
    else:
        carrier.pop(BAGGAGE_HEADER, None)


def get_current_trace_id() -> str | None:
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


def get_current_span_id() -> str | None:
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
