"""MCP Hangar CLI.

Interactive command-line interface for MCP Hangar with subcommands
for initialization, mcp_server management, and health monitoring.

Usage:
    mcp-hangar init          # Interactive setup wizard
    mcp-hangar status        # Show mcp_server health dashboard
    mcp-hangar add <name>    # Add a mcp_server from registry
    mcp-hangar remove <name> # Remove a mcp_server
    mcp-hangar serve         # Start the MCP server (default behavior)
"""

# Re-export legacy CLI types for backward compatibility
from .cli_compat import CLIConfig, parse_args
from .main import app, cli_main

__all__ = ["app", "cli_main", "CLIConfig", "parse_args"]
