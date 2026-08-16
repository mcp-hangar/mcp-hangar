"""The inbound server identity.

This file used to hold `HangarFunctions` and `ServerConfig`, the constructor
arguments of `MCPServerFactory`. The factory had no production call site and was
deleted in #956; the constant below is what survived it, because the shipped
`serve --http` path reads it.
"""

# INBOUND server identity: the ``serverInfo.name`` Hangar reports to its own
# clients, on every surface that carries one (``initialize`` and the SEP-2575
# ``server/discover`` result). One constant because the two used to disagree --
# the factory said "mcp-hangar" while the shipped ``serve --http`` path said
# "mcp-registry", so a client saw a different server depending on which surface
# it asked (#560). Distinct from ``protocol.HANGAR_CLIENT_INFO``, which is the
# OUTBOUND clientInfo Hangar presents to upstream MCP servers.
HANGAR_SERVER_NAME = "mcp-hangar"

__all__ = ["HANGAR_SERVER_NAME"]
