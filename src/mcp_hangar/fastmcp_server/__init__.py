"""The MCP-over-HTTP surface the gateway serves.

This package holds the pieces `serve --http` assembles its MCP server from. It
does not assemble one itself: the composition root is
`mcp_hangar.server.bootstrap`, which registers the tools and wires the modern
surface, and `mcp_hangar.server.lifecycle.mcp_app_for_serving`, which builds the
ASGI app the CLI mounts.

It used to also export `MCPServerFactory` -- a second, parallel construction
path that no shipped code called. Keeping it cost more than the duplication
suggested: a capability wired into one path and not the other looked wired
(#592, #594, #595, #596), and its `/health` and `/ready` routes never matched
the `/health/live` / `/health/ready` / `/health/startup` a running Hangar
actually serves. It was removed across #954, #955 and #956; embedders drive the
gateway through `serve --http` or the bootstrap above.

Endpoints (HTTP mode):
- /health/live   : liveness probe (is the process alive?)
- /health/ready  : readiness probe (can handle traffic?)
- /health/startup: startup probe (is initialization complete?)
- /metrics       : prometheus metrics
- /mcp           : MCP streamable HTTP endpoint
"""

from .config import HANGAR_SERVER_NAME

__all__ = ["HANGAR_SERVER_NAME"]
