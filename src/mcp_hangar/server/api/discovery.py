"""Discovery endpoint handlers for the REST API.

Implements GET/POST endpoints for auto-discovery management:
sources, pending mcp_servers, quarantined mcp_servers, approve/reject.
"""

import re
from typing import cast

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.discovery_commands import (
    DeregisterDiscoverySourceCommand,
    RegisterDiscoverySourceCommand,
    ToggleDiscoverySourceCommand,
    TriggerSourceScanCommand,
    UpdateDiscoverySourceCommand,
)
from ...domain.discovery import DiscoveryMode
from ...domain.exceptions import McpServerNotFoundError
from ..context import get_context
from .middleware import dispatch_command
from .request_body import json_body, missing_fields
from .serializers import HangarJSONResponse

#: The discovery source-management surface (register/update/deregister a source,
#: trigger a scan, toggle enabled) ships in 2.5.0 as **Preview**, not GA: it was
#: broken end-to-end in rc.4 and its behaviour may still change. Every mutating
#: source-management response carries this header so a caller sees the preview
#: status without reading the docs. The read-only discovery flow (list sources,
#: pending, quarantined, approve/reject) is stable and does NOT carry it.
_PREVIEW_HEADERS = {"X-Hangar-Preview": "discovery-source-management"}


class DiscoveryNotConfigured(McpServerNotFoundError):
    """Raised when discovery is requested but not configured.

    Extends McpServerNotFoundError so the middleware maps it to HTTP 404.
    Named DiscoveryNotConfigured so that type(exc).__name__ == "DiscoveryNotConfigured"
    in the API error envelope.
    """

    def __init__(self) -> None:
        # Bypass McpServerNotFoundError.__init__ to set our own message
        from ...domain.exceptions import MCPError

        MCPError.__init__(
            self,
            message="Auto-discovery is not configured on this server.",
            mcp_server_id="",
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
    # The id now comes from get_sources_status() itself (SourceStatus.to_dict),
    # one place that REST and the MCP tool both read -- no special-case backfill
    # here. What this route still owns is the agreement between the listing and
    # its own addressable sub-routes: a source the orchestrator keeps running
    # after DELETE /sources/{id} is still in this listing, but its id no longer
    # names a registered spec, so /sources/{id}/scan and /enable would 404 for
    # an id the listing shows as present. Cross-check membership and strip the id
    # of any source the registry no longer knows, so a listed id is always one
    # the routes accept.
    known_ids = _registered_source_ids()
    if known_ids is not None:
        for source in sources:
            source_id = source.get("id")
            if source_id is not None and source_id not in known_ids:
                source.pop("id", None)
    return HangarJSONResponse({"sources": sources})


def _registered_source_ids() -> set[str] | None:
    """The source ids the DiscoveryRegistry currently knows, or None if unknown.

    None means "cannot determine membership" -- no registry wired -- in which
    case list_sources leaves ids untouched rather than hiding every one. In
    production the registry is always present when discovery is; the guard keeps
    the listing working under partial test wiring.
    """
    registry = getattr(get_context(), "discovery_registry", None)
    if registry is None:
        return None
    try:
        return {spec.source_id for spec in registry.get_all_sources()}
    except (TypeError, AttributeError):
        return None


async def list_pending(request: Request) -> HangarJSONResponse:
    """List mcp_servers pending approval.

    Returns:
        JSON with {"pending": [...]} array of discovered mcp_server dicts, each
        with the ``definition_digest`` an approval binds to.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    from starlette.concurrency import run_in_threadpool

    orchestrator = _require_orchestrator()
    pending = await run_in_threadpool(orchestrator.get_pending_mcp_servers)
    # `definition_digest` is what an approval binds to (#2109).
    return HangarJSONResponse({"pending": [{**p.to_dict(), "definition_digest": p.definition_digest} for p in pending]})


async def list_quarantined(request: Request) -> HangarJSONResponse:
    """List quarantined mcp_servers.

    Returns:
        JSON with {"quarantined": {...}} dict of quarantined mcp_server info.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    from starlette.concurrency import run_in_threadpool

    orchestrator = _require_orchestrator()
    quarantined = await run_in_threadpool(orchestrator.get_quarantined)
    return HangarJSONResponse({"quarantined": quarantined})


#: The parameter an approval binds itself with (#2109), and what it must look
#: like: the full SHA-256 a listing shows, never the 16-character fingerprint.
_BINDING = "definition_digest"
_DIGEST = re.compile(r"[0-9a-f]{64}")
#: Spellings that mean to bind and are not the binding. Ignoring one would
#: approve unbound what the caller meant to bind, so each is refused.
#: Query keys that read as an attempt to bind, compared with every non-letter
#: removed (`definition-digest`, `Definition_Digest`, `fingerprint`, ...).
_NEAR_BINDING = {"definitiondigest", "fingerprint", "digest"}


def _near_binding(key: str) -> bool:
    return re.sub(r"[^a-z]", "", key.lower()) in _NEAR_BINDING


def _refused(error: str, detail: str) -> HangarJSONResponse:
    return HangarJSONResponse({"error": error, "detail": detail}, status_code=400)


async def _approval_binding(request: Request) -> str | None | HangarJSONResponse:
    """The digest an approval binds to, None for an unbound one, or a 400.

    Fails closed: anything that might have meant to bind and cannot be read
    as exactly one digest -- a body that is not a JSON object, a misspelt or
    null key, a query and body that disagree -- is refused rather than read
    as "no binding" and approved unbound. A request with no body and no
    query keeps working, unbound.
    """
    near = sorted(k for k in request.query_params if k != _BINDING and _near_binding(k))
    values: list[object] = list(request.query_params.getlist(_BINDING))
    if (await request.body()).strip():
        body = await json_body(request)
        if (invalid := missing_fields(body)) is not None:
            return invalid
        near += sorted(k for k in body if k != _BINDING)
        if _BINDING in body:
            values.append(body[_BINDING])
    if near:
        return _refused("unknown_fields", f"unknown field(s): {', '.join(near)}; the only one accepted is {_BINDING!r}")
    for value in values:
        if not isinstance(value, str) or not _DIGEST.fullmatch(value):
            return _refused(
                "invalid_definition_digest",
                f"{_BINDING} must be the 64-character lowercase hex digest a listing shows",
            )
    if len(set(values)) > 1:
        return _refused("conflicting_definition_digest", f"the query and body name different {_BINDING} values")
    return cast(str, values[0]) if values else None


async def approve_mcp_server(request: Request) -> HangarJSONResponse:
    """Approve a pending or quarantined mcp_server for registration.

    Path params:
        name: McpServer name to approve.

    Query param or JSON body (optional):
        definition_digest: The ``definition_digest`` of the definition the
            operator reviewed, as ``GET /pending`` and ``GET /quarantined``
            show it (#2109). Given, the approval applies only that definition.
            Absent, whatever waits under the name is applied, as before, and a
            warning is logged. A request with no body keeps working; a body
            that is present must be a JSON object holding only this key.

    Returns:
        JSON with approval result from orchestrator.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
        DiscoveredDefinitionChangedError: The definition waiting is not the one
            with *definition_digest* (-> 409, naming the current digest).
    """
    name = request.path_params["name"]
    orchestrator = _require_orchestrator()
    binding = await _approval_binding(request)
    if isinstance(binding, HangarJSONResponse):
        return binding
    result = await orchestrator.approve_mcp_server(name, definition_digest=binding)
    return HangarJSONResponse(result)


async def reject_mcp_server(request: Request) -> HangarJSONResponse:
    """Reject a pending or quarantined mcp_server.

    Path params:
        name: McpServer name to reject.

    Returns:
        JSON with rejection result from orchestrator.

    Raises:
        DiscoveryNotConfiguredError: If discovery is not configured.
    """
    name = request.path_params["name"]
    orchestrator = _require_orchestrator()
    result = await orchestrator.reject_mcp_server(name)
    return HangarJSONResponse(result)


def _invalid_mode(mode: object) -> HangarJSONResponse | None:
    """A 400 naming the bad mode and the valid spellings, or ``None`` when acceptable.

    ``None`` passes: register_source guards presence via ``missing_fields`` before
    this runs, and update_source treats an absent mode as "no change". Left
    unvalidated, the value reaches ``DiscoveryMode(...)`` in the command handler,
    whose bare ``ValueError`` the middleware reports as a 500 (#987).
    """
    valid = [m.value for m in DiscoveryMode]
    if mode is None or mode in valid:
        return None
    return HangarJSONResponse(
        {"error": "invalid_mode", "detail": f"invalid mode {mode!r}; valid modes: {', '.join(valid)}"},
        status_code=400,
    )


async def register_source(request: Request) -> HangarJSONResponse:
    """Register a new discovery source.

    **Preview** (2.5.0): source management is not yet GA; response carries
    ``X-Hangar-Preview: discovery-source-management``.

    Body:
        source_type: Type of source ("docker", "filesystem", "kubernetes", "entrypoint").
        mode: Discovery mode ("additive" or "authoritative").
        enabled: Whether to activate immediately (default: true).
        config: Source-specific configuration dict (default: {}).

    The source is built and added to the running orchestrator, so it is listed by
    ``GET /discovery/sources`` and used by the next scan. The orchestrator holds one
    source per type: a type already held (from the config or an earlier call) is
    refused with 422, and so is a type that is unknown or unavailable. The returned
    ``source_id`` is the id the listing shows.

    Returns:
        JSON with {"source_id": ..., "registered": true} and HTTP 201.

    Raises:
        DiscoveryNotConfigured: If discovery is not configured.
    """
    _require_orchestrator()  # Guard: discovery must be configured
    body = await json_body(request)
    if (invalid := missing_fields(body, "source_type", "mode")) is not None:
        return invalid
    if (invalid := _invalid_mode(body["mode"])) is not None:
        return invalid
    result = await dispatch_command(
        RegisterDiscoverySourceCommand(
            source_type=body["source_type"],
            mode=body["mode"],
            enabled=body.get("enabled", True),
            config=body.get("config", {}),
        )
    )
    return HangarJSONResponse(result, status_code=201, headers=_PREVIEW_HEADERS)


async def update_source(request: Request) -> HangarJSONResponse:
    """Update an existing discovery source spec.

    **Preview** (2.5.0): source management is not yet GA; response carries
    ``X-Hangar-Preview: discovery-source-management``.

    Path params:
        source_id: UUID of the source to update.

    Body:
        mode: Optional new mode string.
        enabled: Optional new enabled state.
        config: Optional new config dict (replaces entire config).

    Returns:
        JSON with {"source_id": ..., "updated": true}.

    Raises:
        McpServerNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    body = await json_body(request)
    if (invalid := missing_fields(body)) is not None:
        return invalid
    if (invalid := _invalid_mode(body.get("mode"))) is not None:
        return invalid
    result = await dispatch_command(
        UpdateDiscoverySourceCommand(
            source_id=source_id,
            mode=body.get("mode"),
            enabled=body.get("enabled"),
            config=body.get("config"),
        )
    )
    return HangarJSONResponse(result, headers=_PREVIEW_HEADERS)


async def deregister_source(request: Request) -> HangarJSONResponse:
    """Remove a discovery source from the registry.

    **Preview** (2.5.0): source management is not yet GA; response carries
    ``X-Hangar-Preview: discovery-source-management``.

    Path params:
        source_id: UUID of the source to remove.

    Returns:
        JSON with {"source_id": ..., "deregistered": true}.

    Raises:
        McpServerNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    result = await dispatch_command(DeregisterDiscoverySourceCommand(source_id=source_id))
    return HangarJSONResponse(result, headers=_PREVIEW_HEADERS)


async def trigger_scan(request: Request) -> HangarJSONResponse:
    """Trigger an immediate discovery scan for a source.

    **Preview** (2.5.0): source management is not yet GA; response carries
    ``X-Hangar-Preview: discovery-source-management``.

    Path params:
        source_id: UUID of the source to scan.

    Returns:
        JSON with {"source_id": ..., "scan_triggered": true, "mcp_servers_found": int}.

    Raises:
        McpServerNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    result = await dispatch_command(TriggerSourceScanCommand(source_id=source_id))
    return HangarJSONResponse(result, headers=_PREVIEW_HEADERS)


async def toggle_source(request: Request) -> HangarJSONResponse:
    """Enable or disable a discovery source.

    **Preview** (2.5.0): source management is not yet GA; response carries
    ``X-Hangar-Preview: discovery-source-management``.

    Path params:
        source_id: UUID of the source to toggle.

    Body:
        enabled: true to enable, false to disable.

    Returns:
        JSON with {"source_id": ..., "enabled": bool}.

    Raises:
        McpServerNotFoundError: If source_id is not registered (-> 404).
    """
    source_id = request.path_params["source_id"]
    body = await json_body(request)
    if (invalid := missing_fields(body, "enabled")) is not None:
        return invalid
    result = await dispatch_command(
        ToggleDiscoverySourceCommand(
            source_id=source_id,
            enabled=body["enabled"],
        )
    )
    return HangarJSONResponse(result, headers=_PREVIEW_HEADERS)


# Route definitions for mounting in the API router
discovery_routes = [
    # Existing discovery routes (approval workflow)
    Route("/sources", list_sources, methods=["GET"]),
    Route("/pending", list_pending, methods=["GET"]),
    Route("/quarantined", list_quarantined, methods=["GET"]),
    Route("/approve/{name:str}", approve_mcp_server, methods=["POST"]),
    Route("/reject/{name:str}", reject_mcp_server, methods=["POST"]),
    # Discovery source management (DISC-02)
    Route("/sources", register_source, methods=["POST"]),
    Route("/sources/{source_id:str}", update_source, methods=["PUT"]),
    Route("/sources/{source_id:str}", deregister_source, methods=["DELETE"]),
    Route("/sources/{source_id:str}/scan", trigger_scan, methods=["POST"]),
    Route("/sources/{source_id:str}/enable", toggle_source, methods=["PUT"]),
]
