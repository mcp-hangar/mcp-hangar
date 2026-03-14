"""Discovery endpoint handlers for the REST API.

Implements GET/POST endpoints for auto-discovery management:
sources, pending providers, quarantined providers, approve/reject.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...domain.exceptions import ProviderNotFoundError
from ..context import get_context
from .serializers import HangarJSONResponse


class DiscoveryNotConfigured(ProviderNotFoundError):
    """Raised when discovery is requested but not configured.

    Extends ProviderNotFoundError so the middleware maps it to HTTP 404.
    Named DiscoveryNotConfigured so that type(exc).__name__ == "DiscoveryNotConfigured"
    in the API error envelope.
    """

    def __init__(self) -> None:
        # Bypass ProviderNotFoundError.__init__ to set our own message
        from ...domain.exceptions import MCPError

        MCPError.__init__(
            self,
            message="Auto-discovery is not configured on this server.",
            provider_id="",
            operation="discovery",
        )


def _require_orchestrator():
    """Return the discovery orchestrator or raise DiscoveryNotConfiguredError.

    Returns:
        DiscoveryOrchestrator instance.

    Raises:
        DiscoveryNotConfiguredError: If discovery_orchestrator is None.
    """
    ctx = get_context()
    orchestrator = ctx.discovery_orchestrator
    if orchestrator is None:
        raise DiscoveryNotConfigured()
    return orchestrator


async def list_sources(request: Request) -> HangarJSONResponse:
    """List all discovery source statuses.

    Returns:
        JSON with {"sources": [...]} array of source status dicts.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    orchestrator = _require_orchestrator()
    sources = await orchestrator.get_sources_status()
    return HangarJSONResponse({"sources": sources})


async def list_pending(request: Request) -> HangarJSONResponse:
    """List providers pending approval.

    Returns:
        JSON with {"pending": [...]} array of discovered provider dicts.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    from starlette.concurrency import run_in_threadpool

    orchestrator = _require_orchestrator()
    pending = await run_in_threadpool(orchestrator.get_pending_providers)
    return HangarJSONResponse({"pending": [p.to_dict() for p in pending]})


async def list_quarantined(request: Request) -> HangarJSONResponse:
    """List quarantined providers.

    Returns:
        JSON with {"quarantined": {...}} dict of quarantined provider info.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    from starlette.concurrency import run_in_threadpool

    orchestrator = _require_orchestrator()
    quarantined = await run_in_threadpool(orchestrator.get_quarantined)
    return HangarJSONResponse({"quarantined": quarantined})


async def approve_provider(request: Request) -> HangarJSONResponse:
    """Approve a pending provider for registration.

    Path params:
        name: Provider name to approve.

    Returns:
        JSON with approval result from orchestrator.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    name = request.path_params["name"]
    orchestrator = _require_orchestrator()
    result = await orchestrator.approve_provider(name)
    return HangarJSONResponse(result)


async def reject_provider(request: Request) -> HangarJSONResponse:
    """Reject a pending or quarantined provider.

    Path params:
        name: Provider name to reject.

    Returns:
        JSON with rejection result from orchestrator.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    name = request.path_params["name"]
    orchestrator = _require_orchestrator()
    result = await orchestrator.reject_provider(name)
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
discovery_routes = [
    Route("/sources", list_sources, methods=["GET"]),
    Route("/pending", list_pending, methods=["GET"]),
    Route("/quarantined", list_quarantined, methods=["GET"]),
    Route("/approve/{name:str}", approve_provider, methods=["POST"]),
    Route("/reject/{name:str}", reject_provider, methods=["POST"]),
]
