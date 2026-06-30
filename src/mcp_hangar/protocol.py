"""Shared MCP protocol context for the outbound path.

Leaf module (no internal imports) so both the domain startup handshake and the
transport clients can use these without crossing layer boundaries or risking an
import cycle.
"""

from __future__ import annotations

from typing import Any

# MCP protocol version Hangar advertises to upstream MCP servers. Targets the
# 2026-07-28 revision; a legacy upstream downgrades in its initialize response.
SUPPORTED_PROTOCOL_VERSION = "2026-07-28"

# clientInfo Hangar presents to upstream servers.
HANGAR_CLIENT_INFO = {"name": "mcp-registry", "version": "1.0.0"}

# Reverse-DNS _meta keys per the MCP spec namespace (SEP-2575 stateless model).
_META_PROTOCOL_VERSION_KEY = "io.modelcontextprotocol/protocolVersion"
_META_CLIENT_INFO_KEY = "io.modelcontextprotocol/clientInfo"


def inject_protocol_meta(params: dict[str, Any]) -> dict[str, Any]:
    """Return ``params`` with Hangar's protocol context merged into ``params._meta``.

    A stateless upstream (SEP-2575) has no initialize handshake, so the protocol
    version + client info must travel in every request's ``_meta`` instead. This
    returns a new dict and does not mutate the caller's ``params``; existing
    ``_meta`` keys are preserved and caller-set protocol keys win (set-if-absent).
    """
    meta = dict(params.get("_meta") or {})
    meta.setdefault(_META_PROTOCOL_VERSION_KEY, SUPPORTED_PROTOCOL_VERSION)
    meta.setdefault(_META_CLIENT_INFO_KEY, dict(HANGAR_CLIENT_INFO))
    return {**params, "_meta": meta}
