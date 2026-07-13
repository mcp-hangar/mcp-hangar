"""Serve command - Start the MCP Hangar server.

This command starts the MCP server in either stdio or HTTP mode.
It's the default command when mcp-hangar is run without arguments,
maintaining backward compatibility with the original CLI behavior.
"""

import os
from pathlib import Path
from typing import Annotated

import typer


def serve_command(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(
            "--config",
            "-c",
            help="Path to config.yaml file",
            envvar="MCP_CONFIG",
        ),
    ] = None,
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
    unsafe_no_auth: Annotated[
        bool,
        typer.Option(
            "--unsafe-no-auth",
            help="Allow non-loopback HTTP binding without authentication (unsafe)",
            is_flag=True,
        ),
    ] = False,
    cloud_key: Annotated[
        str | None,
        typer.Option(
            "--cloud-key",
            help="License key for Hangar Cloud connectivity",
            envvar="MCP_CLOUD_KEY",
        ),
    ] = None,
    cloud_url: Annotated[
        str | None,
        typer.Option(
            "--cloud-url",
            help="Hangar Cloud API endpoint",
            envvar="MCP_CLOUD_URL",
        ),
    ] = None,
):
    """Start the MCP Hangar server.

    By default, runs in stdio mode for Claude Desktop integration.
    Use --http for HTTP mode with Streamable HTTP transport.

    Examples:
        mcp-hangar serve
        mcp-hangar serve --http --port 8000
        mcp-hangar serve --config config.yaml
        mcp-hangar --config config.yaml serve
    """
    # Get global options
    global_opts = ctx.obj

    # A --config passed to `serve` overrides the top-level option; when it is
    # absent, fall back to the value resolved by the main CLI callback. This
    # lets both `mcp-hangar --config X serve` and `mcp-hangar serve --config X`
    # work.
    resolved_config = config if config is not None else getattr(global_opts, "config", None)

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
        config_path=str(resolved_config) if resolved_config else None,
        log_file=log_file,
        log_level=log_level.upper(),
        json_logs=json_logs,
        unsafe_no_auth=unsafe_no_auth,
        cloud_key=cloud_key,
        cloud_url=cloud_url,
    )

    # Import and run the server
    from ...lifecycle import run_server

    run_server(cli_config)


__all__ = ["serve_command"]
