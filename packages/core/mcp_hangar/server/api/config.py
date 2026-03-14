"""Configuration endpoint handlers for the REST API.

Implements GET /config (current configuration) and POST /config/reload
(hot-reload configuration from file).

Sensitive fields are stripped from GET responses so secrets are never leaked.
"""

import json

from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.commands import ReloadConfigurationCommand
from .middleware import dispatch_command, get_context
from .serializers import HangarJSONResponse

# Field name fragments that indicate a sensitive value.
_SENSITIVE_FRAGMENTS = frozenset({"secret", "key", "token", "password"})


def _sanitize(config: dict) -> dict:
    """Strip sensitive keys from a config dict (non-recursive, top-level only).

    Args:
        config: Raw configuration dictionary.

    Returns:
        Copy of config with sensitive keys removed.
    """
    return {k: v for k, v in config.items() if not any(frag in k.lower() for frag in _SENSITIVE_FRAGMENTS)}


async def get_config(request: Request) -> HangarJSONResponse:
    """Return sanitized current server configuration.

    Reads from the config repository if available, or falls back to a
    minimal dict derived from the provider registry.

    Sensitive fields (containing 'secret', 'key', 'token', 'password') are
    stripped before the response is returned.

    Returns:
        JSON with {"config": {...}} containing sanitized configuration.
    """
    ctx = get_context()
    config_repository = ctx.runtime.config_repository if ctx.runtime else None

    if config_repository is not None:
        from starlette.concurrency import run_in_threadpool

        raw = await run_in_threadpool(config_repository.get_all)
        config_dict = {"providers": [p for p in raw]} if raw else {}
    else:
        # Fallback: minimal operational config
        config_dict = {"providers": []}

    return HangarJSONResponse({"config": _sanitize(config_dict)})


async def reload_config(request: Request) -> HangarJSONResponse:
    """Trigger a hot-reload of server configuration.

    Optional JSON body:
        config_path: Path to the config file (default: None, uses server default).
        graceful: Whether to perform a graceful reload (default: True).

    Returns:
        JSON with {"status": "reloaded", "result": ...} on success.
    """
    config_path = None
    graceful = True
    try:
        body = await request.json()
        config_path = body.get("config_path", None)
        graceful = body.get("graceful", True)
    except (json.JSONDecodeError, ValueError):  # empty body or non-JSON content
        pass

    result = await dispatch_command(
        ReloadConfigurationCommand(
            config_path=config_path,
            graceful=graceful,
            requested_by="api",
        )
    )
    return HangarJSONResponse({"status": "reloaded", "result": result})


# Route definitions for mounting in the API router
config_routes = [
    Route("/", get_config, methods=["GET"]),
    Route("/reload", reload_config, methods=["POST"]),
]
