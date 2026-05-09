"""Backwards-compatibility helpers for the Provider -> McpServer migration."""

from __future__ import annotations


def resolve_legacy_mcp_server_id(mcp_server_id: str | None, kwargs: dict[str, object]) -> str:
    """Resolve mcp_server_id from kwargs, supporting legacy provider_id alias.

    Mutates kwargs by popping provider_id if present.
    """
    if mcp_server_id is not None:
        return mcp_server_id
    legacy_id = kwargs.pop("provider_id", None)
    if isinstance(legacy_id, str):
        return legacy_id
    raise TypeError("Missing required argument: mcp_server_id")
