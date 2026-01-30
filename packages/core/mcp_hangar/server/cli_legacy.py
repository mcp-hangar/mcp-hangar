"""Command Line Interface for MCP Hangar Server.

This module handles CLI argument parsing and environment variable resolution.
It is designed to be pure - no side effects, no logging setup, no server startup.

Usage:
    from mcp_hangar.server.cli import parse_args, CLIConfig

    config = parse_args()
    # or for testing:
    config = parse_args(["--http", "--port", "9000"])
"""

import argparse
from dataclasses import dataclass
from importlib.metadata import version
import os


def _get_version() -> str:
    """Get package version from metadata."""
    try:
        return version("mcp-hangar")
    except Exception:
        return "unknown"


@dataclass(frozen=True)
class CLIConfig:
    """Parsed CLI configuration.

    This is a frozen dataclass to ensure immutability after parsing.
    All values are resolved from CLI arguments and environment variables.
    """

    http_mode: bool
    """Whether to run in HTTP mode (True) or stdio mode (False)."""

    http_host: str
    """Host to bind HTTP server to."""

    http_port: int
    """Port to bind HTTP server to."""

    config_path: str | None
    """Path to config.yaml file."""

    log_file: str | None
    """Path to log file for server output."""

    log_level: str
    """Log level (DEBUG, INFO, WARNING, ERROR, CRITICAL)."""

    json_logs: bool
    """Whether to format logs as JSON."""


def parse_args(args: list[str] | None = None) -> CLIConfig:
    """Parse command line arguments.

    Resolves values in this order (later overrides earlier):
    1. Default values
    2. Environment variables
    3. CLI arguments

    Args:
        args: Optional argument list (for testing). Uses sys.argv if None.

    Returns:
        Parsed CLIConfig dataclass with all resolved values.

    Examples:
        # Default parsing from sys.argv
        config = parse_args()

        # Testing with specific arguments
        config = parse_args(["--http", "--port", "9000"])
        assert config.http_mode is True
        assert config.http_port == 9000
    """
    parser = argparse.ArgumentParser(
        description="MCP Hangar - Production-grade MCP provider registry",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  mcp-hangar --config config.yaml
  mcp-hangar --config config.yaml --http --port 8000
  mcp-hangar --http --host 0.0.0.0 --port 9000

Environment Variables:
  MCP_MODE          Set to "http" to enable HTTP mode
  MCP_HTTP_HOST     HTTP server host (default: 0.0.0.0)
  MCP_HTTP_PORT     HTTP server port (default: 8000)
  MCP_LOG_LEVEL     Log level (default: INFO)
  MCP_JSON_LOGS     Set to "true" for JSON formatted logs
""",
    )

    parser.add_argument(
        "-V",
        "--version",
        action="version",
        version=f"mcp-hangar {_get_version()}",
    )
    parser.add_argument(
        "--http",
        action="store_true",
        help="Run HTTP server mode instead of stdio",
    )
    parser.add_argument(
        "--host",
        type=str,
        default=None,
        help="HTTP server host (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP server port (default: 8000)",
    )
    parser.add_argument(
        "--config",
        type=str,
        default=None,
        help="Path to config.yaml file",
    )
    parser.add_argument(
        "--log-file",
        type=str,
        default=None,
        help="Path to log file for server output",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default=None,
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Log level (default: INFO)",
    )
    parser.add_argument(
        "--json-logs",
        action="store_true",
        help="Format logs as JSON",
    )

    parsed = parser.parse_args(args)

    # Resolve values: defaults -> env -> CLI args
    # Environment variable defaults
    env_http_mode = os.getenv("MCP_MODE", "stdio") == "http"
    env_http_host = os.getenv("MCP_HTTP_HOST", "0.0.0.0")
    env_http_port = int(os.getenv("MCP_HTTP_PORT", "8000"))
    env_log_level = os.getenv("MCP_LOG_LEVEL", "INFO").upper()
    env_json_logs = os.getenv("MCP_JSON_LOGS", "false").lower() == "true"

    # CLI overrides env
    http_mode = parsed.http or env_http_mode
    http_host = parsed.host if parsed.host is not None else env_http_host
    http_port = parsed.port if parsed.port is not None else env_http_port
    log_level = parsed.log_level if parsed.log_level is not None else env_log_level
    json_logs = parsed.json_logs or env_json_logs

    return CLIConfig(
        http_mode=http_mode,
        http_host=http_host,
        http_port=http_port,
        config_path=parsed.config,
        log_file=parsed.log_file,
        log_level=log_level,
        json_logs=json_logs,
    )


__all__ = ["CLIConfig", "parse_args"]
