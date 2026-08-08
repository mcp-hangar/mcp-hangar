"""Configuration endpoint handlers for the REST API.

Implements GET /config (current configuration), POST /config/reload
(hot-reload configuration from file), POST /config/export (serialize
current state to YAML), POST /config/backup (create rotating backup),
and GET /config/diff (diff on-disk vs in-memory state).

Sensitive fields are stripped from GET responses so secrets are never leaked.
"""

import difflib
import json
import os

import yaml
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.routing import Route

from ...application.commands.commands import ReloadConfigurationCommand
from ...domain.exceptions import ConfigurationUnavailableError, ValidationError
from ..config_serializer import serialize_full_config, write_config_backup
from .middleware import dispatch_command, get_context
from .serializers import HangarJSONResponse

# Field name fragments that indicate a sensitive value.
_SENSITIVE_FRAGMENTS = frozenset({"secret", "key", "token", "password"})


def _requested_by(request: Request) -> str:
    """Attribute a mutation to the calling principal, not to the transport.

    Reload was recorded as ``requested_by="api"`` regardless of who triggered
    it, which makes the audit trail useless for the one operation that
    re-applies every governance input. Falls back to ``"api"`` only when auth
    is disabled and there is genuinely no principal to name.
    """
    state = request.scope.get("state")
    auth_context = state.get("auth") if isinstance(state, dict) else getattr(state, "auth", None)
    principal = getattr(auth_context, "principal", None)
    principal_id = getattr(principal, "id", None)
    return str(principal_id) if principal_id is not None else "api"


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
    minimal dict derived from the mcp_server registry.

    Sensitive fields (containing 'secret', 'key', 'token', 'password') are
    stripped before the response is returned.

    Returns:
        JSON with {"config": {...}} containing sanitized configuration.
    """
    ctx = get_context()
    config_repository = ctx.runtime.config_repository if ctx.runtime else None

    if config_repository is not None:
        raw = await config_repository.get_all()
        config_dict = {"mcp_servers": list(raw)} if raw else {}
    else:
        # Fallback: minimal operational config
        config_dict = {"mcp_servers": []}

    return HangarJSONResponse({"config": _sanitize(config_dict)})


async def reload_config(request: Request) -> HangarJSONResponse:
    """Trigger a hot-reload of server configuration.

    Optional JSON body:
        graceful: Whether to perform a graceful reload (default: True).

    ``config_path`` is deliberately NOT accepted. This handler used to pass a
    caller-supplied path straight into ``ReloadConfigurationCommand``, and
    ``ReloadConfigurationHandler`` loads whatever it names. Because an
    ``mcp_servers`` entry carries ``command`` and ``args``, that turned "reload
    my config" into "load an arbitrary file from the server's filesystem and
    start the processes it describes". The reload now always targets the path
    the server was started with.

    A request that still sends ``config_path`` is rejected rather than silently
    ignored: a caller depending on the old behaviour fails loudly instead of
    getting a reload of a different file than it asked for, and an attempt to
    exploit it is visible in the response and the audit log.

    Returns:
        JSON with {"status": "reloaded", "result": ...} on success.
    """
    graceful = True
    try:
        body = await request.json()
    except (json.JSONDecodeError, ValueError):  # empty body or non-JSON content
        body = {}

    if isinstance(body, dict):
        if "config_path" in body:
            raise ValidationError(
                "config_path is not accepted; reload always targets the server's own configuration file",
                field="config_path",
            )
        graceful = body.get("graceful", True)

    result = await dispatch_command(
        ReloadConfigurationCommand(
            config_path=None,
            graceful=graceful,
            requested_by=_requested_by(request),
        )
    )
    return HangarJSONResponse({"status": "reloaded", "result": result})


async def export_config(request: Request) -> HangarJSONResponse:
    """Serialize current in-memory state to a YAML string.

    Calls serialize_full_config() to capture the current mcp_server and group
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
    try:
        backup_path = await run_in_threadpool(write_config_backup, config_path)
    except OSError as error:
        # The backup is written beside the configuration file, so this fails
        # whenever that directory is not writable by the process -- which is
        # every deployment using the published image, where `/app` belongs to
        # root and the gateway runs as `hangar`. It surfaced as a bare 500 and
        # `An internal server error occurred.`, with the real reason
        # (`PermissionError: [Errno 13] ... 'config.yaml.bak1'`) only in the
        # log. That tells the caller the gateway is broken when the gateway is
        # working and the filesystem said no.
        raise ConfigurationUnavailableError(
            f"could not write the backup beside {config_path!r}: {error.strerror or error}. "
            "The backup is written into the configuration file's own directory, which has to be "
            "writable by the gateway process.",
            details={"config_path": config_path, "errno": error.errno},
        ) from error
    return HangarJSONResponse({"path": backup_path})


async def diff_config(request: Request) -> HangarJSONResponse:
    """Diff on-disk config file vs current in-memory state.

    Computes a unified diff between the YAML representation of the config file
    as it exists on disk and the serialised current in-memory mcp_server/group
    state.  The diff is returned as a unified-diff string along with the raw
    dicts for both sides.

    Query parameters:
        config_path: Path to the config file (default: MCP_CONFIG env var or
            ``"config.yaml"``).

    Returns:
        JSON with:
          - ``has_diff``: True if the on-disk and in-memory configs differ.
          - ``diff``: Unified diff string (empty when ``has_diff`` is False).
          - ``on_disk``: Sanitized on-disk config dict (``{}`` if file missing).
          - ``in_memory``: Current in-memory config dict.
    """
    config_path = request.query_params.get("config_path") or os.environ.get("MCP_CONFIG", "config.yaml")

    def _compute_diff() -> dict:
        from ...server.config import load_config_from_file

        # Load on-disk config (best effort -- return empty dict if missing/invalid)
        try:
            on_disk_raw = load_config_from_file(config_path)
        except (FileNotFoundError, ValueError):
            on_disk_raw = {}

        in_memory = serialize_full_config()

        on_disk_yaml = yaml.safe_dump(on_disk_raw, default_flow_style=False, sort_keys=True, allow_unicode=True)
        in_memory_yaml = yaml.safe_dump(in_memory, default_flow_style=False, sort_keys=True, allow_unicode=True)

        diff_lines = list(
            difflib.unified_diff(
                on_disk_yaml.splitlines(keepends=True),
                in_memory_yaml.splitlines(keepends=True),
                fromfile="on-disk",
                tofile="in-memory",
            )
        )
        diff_str = "".join(diff_lines)

        return {
            "has_diff": bool(diff_str),
            "diff": diff_str,
            "on_disk": _sanitize(on_disk_raw),
            "in_memory": _sanitize(in_memory),
        }

    result = await run_in_threadpool(_compute_diff)
    return HangarJSONResponse(result)


# Route definitions for mounting in the API router
config_routes = [
    Route("/", get_config, methods=["GET"]),
    Route("/diff", diff_config, methods=["GET"]),
    Route("/export", export_config, methods=["POST"]),
    Route("/backup", backup_config, methods=["POST"]),
    Route("/reload", reload_config, methods=["POST"]),
]
