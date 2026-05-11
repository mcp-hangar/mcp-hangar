"""Digest computation for MCP tool schemas (SEP-1766).

Produces deterministic SHA-256 fingerprints from tool schema dicts,
enabling drift detection and allowlist-based pinning.

Uses RFC 8785 (JCS) canonicalization for cross-SDK interoperability.
"""

from __future__ import annotations

import hashlib
from typing import Any

import jcs

from mcp_hangar.domain.value_objects.tool_digest import ToolDigest


def _is_meaningful(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, (dict, list, str)) and len(value) == 0:
        return False
    return True


def compute_tool_digest(tool: dict[str, Any]) -> ToolDigest:
    """Compute the canonical SHA-256 digest of a tool schema.

    Canonical form: RFC 8785 JCS-serialized JSON containing only the
    fields {name, description, inputSchema, outputSchema}.
    Fields that are None, empty dict, empty list, or empty string
    are treated as absent and omitted from the canonical payload.

    Args:
        tool: Tool schema dict as returned by MCP tools/list.
              Expected keys: name, description, inputSchema, outputSchema.

    Returns:
        ToolDigest with the computed sha256 hex string.

    Raises:
        ValueError: If tool has no 'name' key or name is not a non-empty string.
    """
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("tool missing required string field 'name'")

    canonical_payload: dict[str, Any] = {"name": name}

    for field in ("description", "inputSchema", "outputSchema"):
        value = tool.get(field)
        if _is_meaningful(value):
            canonical_payload[field] = value

    serialized = jcs.canonicalize(canonical_payload)  # bytes, RFC 8785
    sha256_hex = hashlib.sha256(serialized).hexdigest()

    return ToolDigest(tool_name=name, sha256=sha256_hex)
