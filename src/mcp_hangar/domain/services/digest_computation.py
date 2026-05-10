"""Digest computation for MCP tool schemas (SEP-1766).

Produces deterministic SHA-256 fingerprints from tool schema dicts,
enabling drift detection and allowlist-based pinning.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from mcp_hangar.domain.value_objects.tool_digest import ToolDigest


def compute_tool_digest(tool: dict[str, Any]) -> ToolDigest:
    """Compute the canonical SHA-256 digest of a tool schema.

    Canonical form: JSON with sorted keys, no whitespace separators,
    and only the fields {name, description, inputSchema, outputSchema}.
    Fields that are None/missing are omitted from the canonical payload.

    Args:
        tool: Tool schema dict as returned by MCP tools/list.
              Expected keys: name, description, inputSchema, outputSchema.

    Returns:
        ToolDigest with the computed sha256 hex string.

    Raises:
        ValueError: If tool has no 'name' key.
    """
    name = tool.get("name")
    if not name:
        raise ValueError("tool dict must contain a non-empty 'name' key")

    canonical_payload: dict[str, Any] = {"name": name}

    description = tool.get("description")
    if description is not None:
        canonical_payload["description"] = description

    input_schema = tool.get("inputSchema")
    if input_schema is not None:
        canonical_payload["inputSchema"] = input_schema

    output_schema = tool.get("outputSchema")
    if output_schema is not None:
        canonical_payload["outputSchema"] = output_schema

    serialized = json.dumps(canonical_payload, sort_keys=True, separators=(",", ":"))
    sha256_hex = hashlib.sha256(serialized.encode("utf-8")).hexdigest()

    return ToolDigest(tool_name=name, sha256=sha256_hex)
