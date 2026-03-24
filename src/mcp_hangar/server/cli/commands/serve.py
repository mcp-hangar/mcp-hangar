"""Serve command - Start the MCP Hangar server.

This command starts the MCP server in either stdio or HTTP mode.
It's the default command when mcp-hangar is run without arguments,
maintaining backward compatibility with the original CLI behavior.
"""

import os
from typing import Annotated

import typer

from ..main import GlobalOptions


def serve_command(
    ctx: typer.Context,
    http: Annotated[
        bool,
        typer.Option(
            "--http",
            help="Run in HTTP mode instead of stdio",
            envvar="MCP_MODE",
            is_flag=True,
        ),
    ] = False,
    host: Annotated[
        str,
        typer.Option(
            "--host",
            help="HTTP server host",
            envvar="MCP_HTTP_HOST",
        ),
    ] = "0.0.0.0",
    port: Annotated[
        int,
        typer.Option(
            "--port",
            "-p",
            help="HTTP server port",
            envvar="MCP_HTTP_PORT",
        ),
    ] = 8000,
    log_file: Annotated[
        str | None,
        typer.Option(
            "--log-file",
            help="Path to log file",
        ),
    ] = None,
    log_level: Annotated[
        str,
        typer.Option(
            "--log-level",
            help="Log level",
            envvar="MCP_LOG_LEVEL",
        ),
    ] = "INFO",
    json_logs: Annotated[
        bool,
        typer.Option(
            "--json-logs",
            help="Format logs as JSON",
            envvar="MCP_JSON_LOGS",
            is_flag=True,
        ),
    ] = False,
):
    """Start the MCP Hangar server.

    By default, runs in stdio mode for Claude Desktop integration.
    Use --http for HTTP mode with SSE transport.

    Examples:
        mcp-hangar serve
        mcp-hangar serve --http --port 8000
        mcp-hangar --config config.yaml serve
    """
    # Get global options
    global_opts: GlobalOptions = ctx.obj

    # Build CLIConfig for backward compatibility with existing server code
    from ..cli_compat import CLIConfig

    # Resolve http mode from environment if flag not set
    http_mode = http
    if not http_mode and os.getenv("MCP_MODE", "").lower() == "http":
        http_mode = True

    cli_config = CLIConfig(
        http_mode=http_mode,
        http_host=host,
        http_port=port,
        config_path=str(global_opts.config) if global_opts.config else None,
        log_file=log_file,
        log_level=log_level.upper(),
        json_logs=json_logs,
    )

    # Import and run the server
    from ...lifecycle import run_server

    run_server(cli_config)


__all__ = ["serve_command"]
