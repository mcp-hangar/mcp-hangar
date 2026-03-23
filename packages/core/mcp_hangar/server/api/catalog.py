"""Catalog endpoint handlers for the REST API.

Implements GET/POST/DELETE endpoints for browsing and managing the static
MCP provider catalog, plus a deploy endpoint that registers a catalog entry
as a live provider via CQRS.
"""

import uuid

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.crud_commands import CreateProviderCommand
from ...domain.exceptions import MCPError, ProviderNotFoundError, ValidationError
from ...domain.model.catalog import McpProviderEntry
from ..context import get_context
from .middleware import dispatch_command
from .serializers import HangarJSONResponse


class CatalogNotConfigured(ProviderNotFoundError):
    """Raised when catalog endpoints are called but no catalog is configured.

    Extends ProviderNotFoundError so middleware maps it to HTTP 404.
    """

    def __init__(self) -> None:
        MCPError.__init__(
            self,
            message="Catalog is not configured on this server.",
            provider_id="",
            operation="catalog",
        )


def _require_catalog():
    """Return the catalog repository or raise CatalogNotConfigured.

    Returns:
        McpCatalogRepository instance.

    Raises:
        CatalogNotConfigured: If catalog_repository is None on context.
    """
    ctx = get_context()
    repo = ctx.catalog_repository
    if repo is None:
        raise CatalogNotConfigured()
    return repo


async def list_catalog_entries(request: Request) -> HangarJSONResponse:
    """List catalog entries with optional filtering.

    Query params:
        search: Case-insensitive substring search on name/description.
        tags: Comma-separated list of tags that must ALL be present.

    Returns:
        JSON with {"entries": [...], "total": int}.

    Raises:
        CatalogNotConfigured: If catalog is not configured (-> 404).
    """
    repo = _require_catalog()
    search = request.query_params.get("search") or None
    tags_param = request.query_params.get("tags") or None
    tags = [t.strip() for t in tags_param.split(",") if t.strip()] if tags_param else None

    entries = repo.list_entries(search=search, tags=tags)
    return HangarJSONResponse({"entries": [e.to_dict() for e in entries], "total": len(entries)})


async def get_catalog_entry(request: Request) -> HangarJSONResponse:
    """Get a single catalog entry by ID.

    Path params:
        entry_id: UUID of the catalog entry.

    Returns:
        JSON with entry dict.

    Raises:
        ProviderNotFoundError: If entry_id is not found (-> 404).
        CatalogNotConfigured: If catalog is not configured (-> 404).
    """
    repo = _require_catalog()
    entry_id = request.path_params["entry_id"]
    entry = repo.get_entry(entry_id)
    if entry is None:
        raise ProviderNotFoundError(provider_id=entry_id)
    return HangarJSONResponse(entry.to_dict())


async def add_catalog_entry(request: Request) -> HangarJSONResponse:
    """Add a custom entry to the catalog.

    Body:
        name: Human-readable provider name (required).
        description: Short description (required).
        mode: Provider mode -- "subprocess", "docker", or "remote" (required).
        command: Command list for subprocess mode (optional).
        image: Docker image for docker mode (optional).
        tags: Categorization tags (optional).
        required_env: Required environment variable names (optional).

    Returns:
        JSON with {"entry_id": ..., "added": true} and HTTP 201.

    Raises:
        CatalogNotConfigured: If catalog is not configured (-> 404).
    """
    repo = _require_catalog()
    body = await request.json()

    entry = McpProviderEntry(
        entry_id=str(uuid.uuid4()),
        name=body["name"],
        description=body["description"],
        mode=body["mode"],
        command=body.get("command", []),
        image=body.get("image"),
        tags=body.get("tags", []),
        verified=False,
        source="custom",
        required_env=body.get("required_env", []),
        builtin=False,
    )
    repo.add_entry(entry)
    return HangarJSONResponse({"entry_id": entry.entry_id, "added": True}, status_code=201)


async def remove_catalog_entry(request: Request) -> HangarJSONResponse:
    """Remove a custom catalog entry.

    Path params:
        entry_id: UUID of the entry to remove.

    Returns:
        JSON with {"entry_id": ..., "deleted": true}.

    Raises:
        ProviderNotFoundError: If entry_id is not found (-> 404).
        ValidationError: If the entry is a builtin entry (-> 422).
        CatalogNotConfigured: If catalog is not configured (-> 404).
    """
    repo = _require_catalog()
    entry_id = request.path_params["entry_id"]

    try:
        repo.remove_entry(entry_id)
    except KeyError:
        raise ProviderNotFoundError(provider_id=entry_id)
    except ValueError as exc:
        raise ValidationError(str(exc))

    return HangarJSONResponse({"entry_id": entry_id, "deleted": True})


async def deploy_catalog_entry(request: Request) -> HangarJSONResponse:
    """Deploy a catalog entry as a live provider.

    Dispatches CreateProviderCommand using the catalog entry's configuration.
    Uses entry.name as the provider_id.

    Path params:
        entry_id: UUID of the catalog entry to deploy.

    Returns:
        JSON with {"provider_id": ..., "deployed": true} and HTTP 201.

    Raises:
        ProviderNotFoundError: If entry_id is not found (-> 404).
        CatalogNotConfigured: If catalog is not configured (-> 404).
    """
    repo = _require_catalog()
    entry_id = request.path_params["entry_id"]

    entry = repo.get_entry(entry_id)
    if entry is None:
        raise ProviderNotFoundError(provider_id=entry_id)

    await dispatch_command(
        CreateProviderCommand(
            provider_id=entry.name,
            mode=entry.mode,
            command=entry.command,
            image=entry.image,
            description=entry.description,
        )
    )
    return HangarJSONResponse({"provider_id": entry.name, "deployed": True}, status_code=201)


# Route definitions for mounting in the API router
catalog_routes = [
    Route("/", list_catalog_entries, methods=["GET"]),
    Route("/entries", add_catalog_entry, methods=["POST"]),
    Route("/entries/{entry_id:str}", remove_catalog_entry, methods=["DELETE"]),
    Route("/{entry_id:str}", get_catalog_entry, methods=["GET"]),
    Route("/{entry_id:str}/deploy", deploy_catalog_entry, methods=["POST"]),
]
