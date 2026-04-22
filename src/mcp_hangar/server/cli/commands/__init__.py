"""CLI command modules for MCP Hangar.

Each module implements a subcommand:
- init: Interactive setup wizard
- status: McpServer health dashboard
- add: Add mcp_servers from registry
- remove: Remove mcp_servers
- serve: Start the MCP server
- completion: Shell completion scripts
"""

from . import add, completion, init, remove, serve, status

__all__ = ["init", "status", "add", "remove", "serve", "completion"]
