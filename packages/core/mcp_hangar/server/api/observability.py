"""Observability endpoint handlers for the REST API.

Exposes metrics, audit log, security events, and alert history
for operational visibility. All endpoints are read-only.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.event_handlers import get_alert_handler, get_audit_handler, get_security_handler
from ...metrics import get_metrics
from .serializers import HangarJSONResponse


async def get_metrics_summary(request: Request) -> HangarJSONResponse:
    """Get metrics in Prometheus text format plus a JSON summary.

    Returns:
        JSON with {"prometheus_text": str, "summary": {...}}.
    """
    prometheus_text = get_metrics()

    # Build a lightweight JSON summary by counting lines with known metric prefixes.
    # This avoids parsing the full Prometheus text but gives useful quick numbers.
    summary: dict = {}
    for line in prometheus_text.splitlines():
        if line.startswith("#") or not line.strip():
            continue
        if line.startswith("mcp_hangar_tool_calls_total{"):
            # Each labeled counter line contributes to a running total
            try:
                value = float(line.split()[-1])
                summary["tool_calls_total"] = summary.get("tool_calls_total", 0.0) + value
            except (ValueError, IndexError):
                pass
        elif line.startswith("mcp_hangar_health_checks_total{") or line.startswith("mcp_hangar_health_check_total{"):
            try:
                value = float(line.split()[-1])
                summary["health_checks_total"] = summary.get("health_checks_total", 0.0) + value
            except (ValueError, IndexError):
                pass

    return HangarJSONResponse({"prometheus_text": prometheus_text, "summary": summary})


async def get_audit_log(request: Request) -> HangarJSONResponse:
    """Get audit log records with optional filters.

    Query params:
        provider_id: Optional filter by provider.
        event_type: Optional filter by event type.
        limit: Max records to return (default 100).

    Returns:
        JSON with {"records": [...], "total": int}.
    """
    provider_id = request.query_params.get("provider_id") or None
    event_type = request.query_params.get("event_type") or None
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    limit = min(max(1, limit), 1000)

    handler = get_audit_handler()
    records = handler.query(provider_id=provider_id, event_type=event_type, limit=limit)
    return HangarJSONResponse({"records": [r.to_dict() for r in records], "total": len(records)})


async def get_security_events(request: Request) -> HangarJSONResponse:
    """Get security events from the security handler.

    Query params:
        limit: Max events to return (default 100).

    Returns:
        JSON with {"events": [...], "total": int}.
    """
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    limit = min(max(1, limit), 1000)

    handler = get_security_handler()
    # SecurityEventHandler has a sink; InMemorySecuritySink has .query()
    events = []
    sink = getattr(handler, "_sink", None) or getattr(handler, "sink", None)
    if sink is not None and hasattr(sink, "query"):
        events = sink.query(limit=limit)
    return HangarJSONResponse({"events": [e.to_dict() for e in events], "total": len(events)})


async def get_alert_history(request: Request) -> HangarJSONResponse:
    """Get alert history from the alert handler.

    Query params:
        level: Optional filter by alert level (critical, warning, info).
        limit: Max alerts to return (default 100).

    Returns:
        JSON with {"alerts": [...], "total": int}.
    """
    level_filter = request.query_params.get("level") or None
    try:
        limit = int(request.query_params.get("limit", 100))
    except ValueError:
        limit = 100
    limit = min(max(1, limit), 1000)

    handler = get_alert_handler()
    alerts = handler.alerts_sent
    if level_filter:
        alerts = [a for a in alerts if a.level == level_filter]
    alerts = alerts[-limit:]  # Most recent last, trim to limit

    return HangarJSONResponse({"alerts": [a.to_dict() for a in alerts], "total": len(alerts)})


# Route definitions for mounting in the API router
observability_routes = [
    Route("/metrics", get_metrics_summary, methods=["GET"]),
    Route("/audit", get_audit_log, methods=["GET"]),
    Route("/security", get_security_events, methods=["GET"]),
    Route("/alerts", get_alert_history, methods=["GET"]),
]
