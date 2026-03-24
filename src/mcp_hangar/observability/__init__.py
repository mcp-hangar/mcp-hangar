"""Observability module for MCP Hangar.

Provides unified observability stack:
- OpenTelemetry tracing
- Extended metrics
- Health endpoints
- Log correlation

Usage:
    from mcp_hangar.observability import init_tracing, get_tracer

    init_tracing(service_name="mcp-hangar")
    tracer = get_tracer(__name__)

    with tracer.start_as_current_span("operation") as span:
        span.set_attribute("provider.id", provider_id)
        # ... do work
"""

from mcp_hangar.observability.health import get_health_endpoint, HealthCheck, HealthEndpoint, HealthStatus
from mcp_hangar.observability.metrics import CircuitState, get_observability_metrics, ObservabilityMetrics
from mcp_hangar.observability.tracing import (
    extract_trace_context,
    get_current_span_id,
    get_current_trace_id,
    get_tracer,
    init_tracing,
    inject_trace_context,
    shutdown_tracing,
    trace_span,
    trace_tool_invocation,
)

__all__ = [
    # Tracing
    "init_tracing",
    "shutdown_tracing",
    "get_tracer",
    "trace_tool_invocation",
    "trace_span",
    "inject_trace_context",
    "extract_trace_context",
    "get_current_trace_id",
    "get_current_span_id",
    # Metrics
    "ObservabilityMetrics",
    "get_observability_metrics",
    "CircuitState",
    # Health
    "HealthStatus",
    "HealthCheck",
    "HealthEndpoint",
    "get_health_endpoint",
]
