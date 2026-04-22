"""System info endpoint handler for the REST API.

Implements:
- GET /system returning system-wide metrics, uptime, and version.
- GET /system/me returning current user authentication status.
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

    Dispatches GetSystemMetricsQuery for current mcp_server/tool metrics,
    then augments with uptime and package version.

    Returns:
        JSON with {"system": {...}} containing:
            - All SystemMetrics fields (total_mcp_servers, mcp_servers_by_state, etc.)
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


async def get_current_user(request: Request) -> HangarJSONResponse:
    """Return current user auth status. Used by SPA to check authentication.

    When auth middleware is active, request.state.auth is populated with the
    authenticated principal. When auth is not enabled, request.state.auth
    will be absent and the response indicates unauthenticated (no login required).

    Returns:
        JSON with authenticated status and optional principal info.
    """
    auth = getattr(request.state, "auth", None)
    if auth is None:
        return HangarJSONResponse({"authenticated": False, "principal": None})
    return HangarJSONResponse(
        {
            "authenticated": True,
            "principal": {
                "id": str(auth.principal.id) if hasattr(auth, "principal") else "unknown",
                "type": auth.principal.type.value if hasattr(auth.principal, "type") else "unknown",
            },
        }
    )


# Route definitions for mounting in the API router
system_routes = [
    Route("/", get_system_info, methods=["GET"]),
    Route("/me", get_current_user, methods=["GET"]),
]
