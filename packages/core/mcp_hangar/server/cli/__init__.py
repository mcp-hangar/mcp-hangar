"""MCP Hangar CLI.

Interactive command-line interface for MCP Hangar with subcommands
for initialization, provider management, and health monitoring.

Usage:
    mcp-hangar init          # Interactive setup wizard
    mcp-hangar status        # Show provider health dashboard
    mcp-hangar add <name>    # Add a provider from registry
    mcp-hangar remove <name> # Remove a provider
    mcp-hangar serve         # Start the MCP server (default behavior)
"""

# Re-export legacy CLI types for backward compatibility
from .cli_compat import CLIConfig, parse_args
from .main import app, cli_main

__all__ = ["app", "cli_main", "CLIConfig", "parse_args"]
