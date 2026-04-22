"""Config serializer -- inverse of server/config.py.

This module serializes in-memory mcp_server and group state back to a
YAML-compatible dict structure, mirroring the format that server/config.py
reads on startup.  It is the persistence-side of the CRUD cycle.

Public API:
    serialize_mcp_servers(mcp_servers=None) -> dict[str, Any]
    serialize_groups(groups=None) -> dict[str, Any]
    serialize_execution_config() -> dict[str, Any]
    serialize_full_config(mcp_servers=None, groups=None) -> dict[str, Any]
    write_config_backup(config_path: str) -> str
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from .context import get_context

if TYPE_CHECKING:
    from ..domain.model.mcp_server import McpServer
    from ..domain.model.mcp_server_group import McpServerGroup


def serialize_mcp_servers(mcp_servers: dict[str, McpServer] | None = None) -> dict[str, Any]:
    """Return a YAML-compatible snapshot of all mcp_servers.

    Args:
        mcp_servers: Optional explicit dict of ``{mcp_server_id: McpServer}``.  When
            *None* the live application context is queried via ``get_context()``.

    Returns:
        Dict mapping each mcp_server ID to its ``to_config_dict()`` output.
    """
    if mcp_servers is None:
        ctx = get_context()
        mcp_servers = ctx.repository.get_all()
    return {mcp_server_id: mcp_server.to_config_dict() for mcp_server_id, mcp_server in mcp_servers.items()}


def serialize_groups(groups: dict[str, McpServerGroup] | None = None) -> dict[str, Any]:
    """Return a YAML-compatible snapshot of all mcp_server groups.

    Args:
        groups: Optional explicit dict of ``{group_id: McpServerGroup}``.  When
            *None* the live application context is queried via ``get_context()``.

    Returns:
        Dict mapping each group ID to its ``to_config_dict()`` output.
    """
    if groups is None:
        ctx = get_context()
        groups = ctx.groups
    return {group_id: group.to_config_dict() for group_id, group in groups.items()}


def serialize_execution_config() -> dict[str, Any]:
    """Return the execution/concurrency section of the config.

    Reads live values from the ConcurrencyManager singleton.  Only includes
    non-zero (non-default) values so that the output remains minimal and
    idempotent on round-trip.

    Returns:
        Dict suitable for the ``"execution"`` top-level key, may be empty
        if all values are at their defaults.
    """
    try:
        from .tools.batch.concurrency import get_concurrency_manager

        manager = get_concurrency_manager()
        section: dict[str, Any] = {}
        if manager.global_limit != 0:
            section["max_concurrency"] = manager.global_limit
        if manager.default_mcp_server_limit != 0:
            section["default_mcp_server_concurrency"] = manager.default_mcp_server_limit
        return section
    except Exception:  # noqa: BLE001 -- fault-barrier: concurrency unavailable should not break serializer
        return {}


def serialize_full_config(
    mcp_servers: dict[str, McpServer] | None = None,
    groups: dict[str, McpServerGroup] | None = None,
) -> dict[str, Any]:
    """Return the complete config as a YAML-compatible dict.

    Includes the ``"mcp_servers"`` section (mcp_servers + groups merged) and any
    additional top-level sections that were present in the original config file
    (e.g. ``event_store``, ``auth``, ``catalog``, ``discovery``,
    ``config_reload``).  The ``execution`` section is always reconstructed from
    the live ConcurrencyManager state.

    Args:
        mcp_servers: Optional explicit mcp_servers dict.  Passed through to
            :func:`serialize_mcp_servers`.
        groups: Optional explicit groups dict.  Passed through to
            :func:`serialize_groups`.

    Returns:
        Full config dict with ``"mcp_servers"`` key and any additional
        top-level sections from the original loaded config.
    """
    serialized_mcp_servers = serialize_mcp_servers(mcp_servers)
    serialized_groups = serialize_groups(groups)

    config: dict[str, Any] = {"mcp_servers": {**serialized_mcp_servers, **serialized_groups}}

    # Append execution section from live ConcurrencyManager state
    execution = serialize_execution_config()
    if execution:
        config["execution"] = execution

    # Pass through other top-level sections from the original config so that
    # the exported YAML can be used as a drop-in replacement for config.yaml.
    # Sections that are purely runtime-derived (mcp_servers, execution) are
    # handled above; everything else is preserved verbatim.
    _PASSTHROUGH_KEYS = frozenset({"event_store", "auth", "catalog", "discovery", "config_reload", "logging"})
    try:
        ctx = get_context()
        stored = getattr(ctx, "full_config", {}) or {}
        for key in _PASSTHROUGH_KEYS:
            if key in stored and key not in config:
                config[key] = stored[key]
    except Exception:  # noqa: BLE001 -- fault-barrier: missing context must not break serialization
        pass

    return config


def _build_snapshot_metadata(config: dict[str, Any]) -> dict[str, Any]:
    """Build a snapshot metadata block for embedding in backup files.

    The metadata is stored under the ``__snapshot__`` key and contains
    information useful for identifying and validating a backup without
    fully parsing it.

    Args:
        config: The full config dict that will be written to the backup.

    Returns:
        Dict with ``timestamp``, ``mcp_server_count``, and ``group_count`` keys.
    """
    mcp_servers_section = config.get("mcp_servers", {}) or {}
    mcp_server_count = sum(1 for v in mcp_servers_section.values() if isinstance(v, dict) and v.get("mode") != "group")
    group_count = sum(1 for v in mcp_servers_section.values() if isinstance(v, dict) and v.get("mode") == "group")
    return {
        "timestamp": datetime.datetime.now(datetime.UTC).isoformat(),
        "mcp_server_count": mcp_server_count,
        "group_count": group_count,
    }


def write_config_backup(config_path: str) -> str:
    """Rotate bak1..bak5 and write current state as the new bak1.

    Rotation order (oldest first):
        bak4 -> bak5  (bak5 is overwritten / dropped)
        bak3 -> bak4
        bak2 -> bak3
        bak1 -> bak2
        <new> -> bak1

    The backup file embeds a ``__snapshot__`` metadata block with the
    creation timestamp, mcp_server count, and group count so that backups
    can be audited without a live server.  The ``__snapshot__`` key is
    ignored by ``load_config_from_file`` because it is not a standard
    config section.

    Args:
        config_path: Absolute or relative path to the config YAML file.
            Backup files are created alongside it with ``.bakN`` suffixes.

    Returns:
        String path of the newly written bak1 file.
    """
    base = Path(config_path)
    # Rotate: bak4->bak5, bak3->bak4, bak2->bak3, bak1->bak2
    for i in range(5, 1, -1):
        older = base.parent / f"{base.name}.bak{i}"
        newer = base.parent / f"{base.name}.bak{i - 1}"
        if newer.exists():
            newer.rename(older)
    backup_path = base.parent / f"{base.name}.bak1"
    snapshot = serialize_full_config()
    snapshot["__snapshot__"] = _build_snapshot_metadata(snapshot)
    content = yaml.safe_dump(
        snapshot,
        default_flow_style=False,
        sort_keys=True,
        allow_unicode=True,
    )
    backup_path.write_text(content, encoding="utf-8")
    return str(backup_path)
