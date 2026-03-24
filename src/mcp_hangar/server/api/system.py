"""System info endpoint handler for the REST API.

Implements GET /system returning system-wide metrics, uptime, and version.
"""

import time

from starlette.requests import Request
from starlette.routing import Route

from ...application.queries.queries import GetSystemMetricsQuery
from .middleware import dispatch_query
from .serializers import HangarJSONResponse

# Module-level start time for uptime calculation
_START_TIME = time.time()


async def get_system_info(request: Request) -> HangarJSONResponse:
    """Return system info including metrics, uptime, and version.

    Dispatches GetSystemMetricsQuery for current provider/tool metrics,
    then augments with uptime and package version.

    Returns:
        JSON with {"system": {...}} containing:
            - All SystemMetrics fields (total_providers, providers_by_state, etc.)
            - uptime_seconds: seconds since server process started
            - version: installed mcp-hangar package version
    """
    metrics = await dispatch_query(GetSystemMetricsQuery())

    try:
        import mcp_hangar

        version = mcp_hangar.__version__
    except (ImportError, AttributeError):
        version = "0.0.0.dev"

    uptime_seconds = time.time() - _START_TIME

    system_data = {
        **metrics.to_dict(),
        "uptime_seconds": uptime_seconds,
        "version": version,
    }

    return HangarJSONResponse({"system": system_data})


# Route definitions for mounting in the API router
system_routes = [
    Route("/", get_system_info, methods=["GET"]),
]
