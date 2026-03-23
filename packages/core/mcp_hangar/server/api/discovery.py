"""Discovery endpoint handlers for the REST API.

Implements GET/POST endpoints for auto-discovery management:
sources, pending providers, quarantined providers, approve/reject.
"""

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
    TriggerSourceScanCommand,
    UpdateDiscoverySourceCommand,
)
from ...domain.exceptions import ProviderNotFoundError
from ..context import get_context
from .middleware import dispatch_command
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


async def register_source(request: Request) -> HangarJSONResponse:
    """Register a new discovery source.

    Body:
        source_type: Type of source ("docker", "filesystem", "kubernetes", "entrypoint").
        mode: Discovery mode ("additive" or "authoritative").
        enabled: Whether to activate immediately (default: true).
        config: Source-specific configuration dict (default: {}).

    Returns:
        JSON with {"source_id": ..., "registered": true} and HTTP 201.

    Raises:
        DiscoveryNotConfigured: If discovery is not configured.
    """
    _require_orchestrator()  # Guard: discovery must be configured
    body = await request.json()
    result = await dispatch_command(
        RegisterDiscoverySourceCommand(
            source_type=body["source_type"],
            mode=body["mode"],
            enabled=body.get("enabled", True),
            config=body.get("config", {}),
        )
    )
    return HangarJSONResponse(result, status_code=201)


async def update_source(request: Request) -> HangarJSONResponse:
    """Update an existing discovery source spec.

    Path params:
        source_id: UUID of the source to update.

    Body:
        mode: Optional new mode string.
        enabled: Optional new enabled state.
        config: Optional new config dict (replaces entire config).

    Returns:
        JSON with {"source_id": ..., "updated": true}.

    Raises:
        ProviderNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    body = await request.json()
    result = await dispatch_command(
        UpdateDiscoverySourceCommand(
            source_id=source_id,
            mode=body.get("mode"),
            enabled=body.get("enabled"),
            config=body.get("config"),
        )
    )
    return HangarJSONResponse(result)


async def deregister_source(request: Request) -> HangarJSONResponse:
    """Remove a discovery source from the registry.

    Path params:
        source_id: UUID of the source to remove.

    Returns:
        JSON with {"source_id": ..., "deregistered": true}.

    Raises:
        ProviderNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    result = await dispatch_command(DeregisterDiscoverySourceCommand(source_id=source_id))
    return HangarJSONResponse(result)


async def trigger_scan(request: Request) -> HangarJSONResponse:
    """Trigger an immediate discovery scan for a source.

    Path params:
        source_id: UUID of the source to scan.

    Returns:
        JSON with {"source_id": ..., "scan_triggered": true, "providers_found": int}.

    Raises:
        ProviderNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    result = await dispatch_command(TriggerSourceScanCommand(source_id=source_id))
    return HangarJSONResponse(result)


async def toggle_source(request: Request) -> HangarJSONResponse:
    """Enable or disable a discovery source.

    Path params:
        source_id: UUID of the source to toggle.

    Body:
        enabled: true to enable, false to disable.

    Returns:
        JSON with {"source_id": ..., "enabled": bool}.

    Raises:
        ProviderNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    body = await request.json()
    result = await dispatch_command(
        ToggleDiscoverySourceCommand(
            source_id=source_id,
            enabled=body["enabled"],
        )
    )
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
discovery_routes = [
    # Existing discovery routes (approval workflow)
    Route("/sources", list_sources, methods=["GET"]),
    Route("/pending", list_pending, methods=["GET"]),
    Route("/quarantined", list_quarantined, methods=["GET"]),
    Route("/approve/{name:str}", approve_provider, methods=["POST"]),
    Route("/reject/{name:str}", reject_provider, methods=["POST"]),
    # Discovery source management (DISC-02)
    Route("/sources", register_source, methods=["POST"]),
    Route("/sources/{source_id:str}", update_source, methods=["PUT"]),
    Route("/sources/{source_id:str}", deregister_source, methods=["DELETE"]),
    Route("/sources/{source_id:str}/scan", trigger_scan, methods=["POST"]),
    Route("/sources/{source_id:str}/enable", toggle_source, methods=["PUT"]),
]
