"""Behavioral report REST endpoint -- BSL 1.1 licensed.

Provides ``GET /providers/{provider_id}/behavioral-report?format=json|pdf``
for exporting per-provider behavioral reports as JSON or PDF. Returns 403
when enterprise behavioral modules are not loaded.

See enterprise/LICENSE.BSL for license terms.
"""

from __future__ import annotations

import structlog
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

logger = structlog.get_logger(__name__)


def _get_report_generator():
    """Retrieve the report generator from application context.

    Returns:
        BehavioralReportGenerator instance or None if not available.
    """
    from mcp_hangar.server.context import get_context

    ctx = get_context()
    return getattr(ctx, "report_generator", None)


async def get_behavioral_report(request: Request) -> Response:
    """Handle GET /providers/{provider_id}/behavioral-report.

    Query parameters:
        format: ``json`` (default) or ``pdf``.

    Returns:
        JSON report dict, PDF bytes, 400 for invalid format,
        or 403 when enterprise modules are not loaded.
    """
    provider_id = request.path_params["provider_id"]
    fmt = request.query_params.get("format", "json")

    report_generator = _get_report_generator()
    if report_generator is None:
        return JSONResponse(
            {
                "error": "Enterprise behavioral profiling modules not loaded. A valid license key is required.",
            },
            status_code=403,
        )

    if fmt not in ("json", "pdf"):
        return JSONResponse(
            {"error": f"Unsupported format: {fmt}. Use 'json' or 'pdf'."},
            status_code=400,
        )

    if fmt == "json":
        report = report_generator.generate_json(provider_id)
        return JSONResponse(report)

    # PDF generation may be CPU-intensive; run in threadpool to avoid
    # blocking the ASGI event loop.
    pdf_bytes = await run_in_threadpool(report_generator.generate_pdf, provider_id)
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename=behavioral-report-{provider_id}.pdf",
        },
    )


behavioral_report_routes = [
    Route(
        "/{provider_id}/behavioral-report",
        get_behavioral_report,
        methods=["GET"],
    ),
]
