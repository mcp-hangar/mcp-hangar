"""CLI command modules for MCP Hangar.

Each module implements a subcommand:
- init: Interactive setup wizard
- status: MCP server health dashboard
- add: Add MCP servers from registry
- remove: Remove MCP servers
- serve: Start the MCP server
- completion: Shell completion scripts
- auth: Authentication administration (bootstrap-admin)
"""

from . import add, auth, completion, init, remove, serve, status

__all__ = ["init", "status", "add", "remove", "serve", "completion", "auth"]
