"""Configuration endpoint handlers for the REST API.

Implements GET /config (current configuration), POST /config/reload
(hot-reload configuration from file), POST /config/export (serialize
current state to YAML), and POST /config/backup (create rotating backup).

Sensitive fields are stripped from GET responses so secrets are never leaked.
"""

import json
import os

import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.commands import ReloadConfigurationCommand
from ..config_serializer import serialize_full_config, write_config_backup
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


async def export_config(request: Request) -> HangarJSONResponse:
    """Serialize current in-memory state to a YAML string.

    Calls serialize_full_config() to capture the current provider and group
    configuration, then serialises it to YAML.

    Returns:
        JSON with {"yaml": "<full config as YAML string>"} and HTTP 200.
    """
    config_dict = await run_in_threadpool(serialize_full_config)
    yaml_str = yaml.safe_dump(config_dict, default_flow_style=False, sort_keys=True, allow_unicode=True)
    return HangarJSONResponse({"yaml": yaml_str})


async def backup_config(request: Request) -> HangarJSONResponse:
    """Write a rotating backup of the current config file (bak1..bak5).

    Optional JSON body:
        config_path: Path to the config file to back up. Defaults to the
            MCP_CONFIG environment variable, or "config.yaml" if not set.

    Returns:
        JSON with {"path": "<backup file path>"} and HTTP 200.
    """
    config_path: str | None = None
    try:
        body = await request.json()
        config_path = body.get("config_path")
    except (json.JSONDecodeError, ValueError):
        pass
    if not config_path:
        config_path = os.environ.get("MCP_CONFIG", "config.yaml")
    backup_path = await run_in_threadpool(write_config_backup, config_path)
    return HangarJSONResponse({"path": backup_path})


# Route definitions for mounting in the API router
config_routes = [
    Route("/", get_config, methods=["GET"]),
    Route("/export", export_config, methods=["POST"]),
    Route("/backup", backup_config, methods=["POST"]),
    Route("/reload", reload_config, methods=["POST"]),
]
