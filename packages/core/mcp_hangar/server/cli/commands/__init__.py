"""CLI command modules for MCP Hangar.

Each module implements a subcommand:
- init: Interactive setup wizard
- status: Provider health dashboard
- add: Add providers from registry
- remove: Remove providers
- serve: Start the MCP server
- completion: Shell completion scripts
"""

from . import add, completion, init, remove, serve, status

__all__ = ["init", "status", "add", "remove", "serve", "completion"]
