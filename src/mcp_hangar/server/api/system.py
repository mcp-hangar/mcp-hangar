"""System info endpoint handler for the REST API.

Implements:
- GET /system returning system-wide metrics, uptime, and version.
- GET /system/me returning current user authentication status.
"""

import time
from typing import Any

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
        "instance": _instance_info(),
    }

    return HangarJSONResponse({"system": system_data})


def _instance_info() -> dict[str, Any]:
    """Which replica answered, and what its numbers are numbers *of*.

    Everything above this line is one replica's view: the state of the servers
    it runs, the metrics it counted, the uptime of this process. With one
    gateway that distinction does not exist and the field is noise. With three
    it is the difference between "the fleet reports 12 calls" and "the pod you
    reached reports 12 calls", and an operator has no way to tell which they are
    reading unless it says so.

    `manages_fleet` is the honest name for what the lease means from outside:
    this is the instance running discovery, garbage collection and TTL
    deregistration right now. Two replicas answering `false` while none answers
    `true` is a fleet with nothing converging it, which is worth being able to
    see directly rather than inferring from what has stopped happening.

    `rate_limits_are_per_instance` states the scope of the configured limit
    rather than quietly multiplying it: with three replicas, a configured 10 rps
    admits 30 across the fleet. Dividing the number by the replica count drifts
    exactly when it matters -- a rollout runs N+1 replicas, a failure runs N-1 --
    and a shared token bucket puts a database round trip on the path of every
    call. A fleet-wide limit belongs at the ingress, where the fleet has one
    entrance (#790, phase 3.1).
    """
    from ...domain.events import current_instance_id
    from ..bootstrap.coordination import get_lease_keeper

    keeper = get_lease_keeper()
    return {
        "instance_id": current_instance_id(),
        "coordinates_with_peers": keeper is not None,
        "manages_fleet": True if keeper is None else keeper.may_manage(),
        "rate_limits_are_per_instance": True,
    }


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
