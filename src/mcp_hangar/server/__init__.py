"""MCP Hangar Server.

Production-grade MCP provider registry with lazy loading, health monitoring,
auto-discovery, and container support.

Usage:
    # CLI
    mcp-hangar --config config.yaml
    mcp-hangar --config config.yaml --http --port 8000

    # Programmatic
    from mcp_hangar.server import main
    main()

    # Or with more control
    from mcp_hangar.server import parse_args, run_server
    cli_config = parse_args(["--http", "--port", "9000"])
    run_server(cli_config)
"""

from mcp.server.fastmcp import FastMCP

# Public API imports
from .bootstrap import (  # Internal functions exported for backward compatibility / testing
    _auto_add_volumes,
    _create_background_workers,
    _create_discovery_source,
    _ensure_data_dir,
    _init_cqrs,
    _init_event_handlers,
    _init_knowledge_base,
    _init_retry_config,
    _init_saga,
    _register_all_tools,
    ApplicationContext,
    bootstrap,
    GC_WORKER_INTERVAL_SECONDS,
    HEALTH_CHECK_INTERVAL_SECONDS,
)
from .cli_legacy import CLIConfig, parse_args
from .config import load_config, load_config_from_file, load_configuration
from .lifecycle import run_server, ServerLifecycle
from .state import COMMAND_BUS, EVENT_BUS, get_runtime, GROUPS, PROVIDER_REPOSITORY, PROVIDERS, QUERY_BUS
from .tools import hangar_list

# Backward compatibility: expose _parse_args as alias
_parse_args = parse_args


# Backward compatibility: expose _start_background_workers
def _start_background_workers() -> None:
    """Start GC and health check background workers.

    DEPRECATED: Use bootstrap() and ServerLifecycle instead.
    This function is kept for backward compatibility only.
    """
    workers = _create_background_workers()
    for worker in workers:
        worker.start()


def main():
    """CLI entry point for the registry server.

    This is the legacy entry point that uses argparse.
    The new recommended entry point is cli_main() which uses typer
    and provides subcommands (init, status, add, remove, serve).

    For backward compatibility, this function still works and defaults
    to server mode.
    """
    cli_config = parse_args()
    run_server(cli_config)


def cli_main():
    """New CLI entry point with subcommands.

    This is the recommended entry point that provides:
    - mcp-hangar init: Interactive setup wizard
    - mcp-hangar status: Provider health dashboard
    - mcp-hangar add: Add providers from registry
    - mcp-hangar remove: Remove providers
    - mcp-hangar serve: Start the MCP server (default)
    """
    from .cli import cli_main as _cli_main

    _cli_main()


# FastMCP server instance for backward compatibility
# Note: This is lazily created by bootstrap() now
mcp = FastMCP("mcp-hangar")


__all__ = [
    # Entry points
    "main",
    "cli_main",
    "run_server",
    # Bootstrap
    "bootstrap",
    "ApplicationContext",
    # CLI
    "parse_args",
    "CLIConfig",
    # Config
    "load_config",
    "load_config_from_file",
    "load_configuration",
    # Lifecycle
    "ServerLifecycle",
    # State (backward compatibility)
    "PROVIDERS",
    "GROUPS",
    "PROVIDER_REPOSITORY",
    "COMMAND_BUS",
    "QUERY_BUS",
    "EVENT_BUS",
    "get_runtime",
    # Tools
    "hangar_list",
    # MCP server instance
    "mcp",
    # Constants
    "GC_WORKER_INTERVAL_SECONDS",
    "HEALTH_CHECK_INTERVAL_SECONDS",
    # Internal functions (backward compatibility / testing)
    "_parse_args",
    "_ensure_data_dir",
    "_init_event_handlers",
    "_init_cqrs",
    "_init_saga",
    "_init_retry_config",
    "_init_knowledge_base",
    "_auto_add_volumes",
    "_create_discovery_source",
    "_create_background_workers",
    "_start_background_workers",
    "_register_all_tools",
]
