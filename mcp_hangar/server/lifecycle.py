"""Server Lifecycle Management.

This module handles starting, running, and stopping the MCP Hangar server.
It manages signal handling for graceful shutdown.

The lifecycle flow:
1. Setup logging based on CLI config
2. Bootstrap application
3. Start background components
4. Run appropriate server mode (stdio or HTTP)
5. Handle shutdown on exit/signal
"""

import asyncio
from pathlib import Path
import signal
import sys
from typing import TYPE_CHECKING

import yaml

from ..logging_config import get_logger, setup_logging
from .bootstrap import ApplicationContext, bootstrap
from .cli import CLIConfig
from .config import load_config_from_file
from .state import get_discovery_orchestrator

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)


class ServerLifecycle:
    """Manages server start/stop lifecycle.

    This class coordinates the startup and shutdown of all server components
    including background workers, discovery orchestrator, and the MCP server.
    """

    def __init__(self, context: ApplicationContext):
        """Initialize server lifecycle.

        Args:
            context: Fully initialized ApplicationContext from bootstrap.
        """
        self._context = context
        self._running = False
        self._shutdown_requested = False

    @property
    def is_running(self) -> bool:
        """Check if server is running."""
        return self._running

    def start(self) -> None:
        """Start all background components.

        Starts:
        - Background workers (GC, health check)
        - Discovery orchestrator (if enabled)

        Does NOT start the MCP server - that's handled by run_stdio() or run_http().
        """
        if self._running:
            logger.warning("server_lifecycle_already_running")
            return

        self._running = True
        logger.info("server_lifecycle_start")

        # Start background workers
        for worker in self._context.background_workers:
            worker.start()

        logger.info(
            "background_workers_started",
            workers=[w.task for w in self._context.background_workers],
        )

        # Start discovery orchestrator
        if self._context.discovery_orchestrator:
            asyncio.run(self._context.discovery_orchestrator.start())
            stats = self._context.discovery_orchestrator.get_stats()
            logger.info("discovery_started", sources_count=stats["sources_count"])

    def run_stdio(self) -> None:
        """Run MCP server in stdio mode. Blocks until exit.

        This is the standard mode for Claude Desktop, Cursor, and other
        MCP clients that communicate via stdin/stdout.
        """
        logger.info("starting_stdio_server")
        try:
            self._context.mcp_server.run()
        except KeyboardInterrupt:
            logger.info("stdio_server_shutdown", reason="keyboard_interrupt")
        except Exception as e:
            logger.critical(
                "fatal_server_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            sys.exit(1)

    def run_http(self, host: str, port: int) -> None:
        """Run MCP server in HTTP mode. Blocks until exit.

        This mode is compatible with LM Studio and other MCP HTTP clients.

        Endpoints:
        - /mcp: Streamable HTTP MCP endpoint (POST/GET)

        Args:
            host: Host to bind to.
            port: Port to bind to.
        """
        import uvicorn

        logger.info("starting_http_server", host=host, port=port)

        # Update FastMCP settings for HTTP mode
        mcp_server = self._context.mcp_server
        mcp_server.settings.host = host
        mcp_server.settings.port = port

        # Get the Starlette app from FastMCP
        starlette_app = mcp_server.streamable_http_app()

        # Configure uvicorn with log_config=None to disable default uvicorn logging
        # Our structlog configuration will handle all logging uniformly
        config = uvicorn.Config(
            starlette_app,
            host=host,
            port=port,
            log_config=None,  # Disable uvicorn's default logging
            access_log=False,  # Disable access logs (we'll handle them via structlog if needed)
        )

        async def run_server():
            server = uvicorn.Server(config)
            logger.info("http_server_started", host=host, port=port, endpoint="/mcp")
            await server.serve()
            logger.info("http_server_stopped")

        try:
            asyncio.run(run_server())
        except KeyboardInterrupt:
            logger.info("http_server_shutdown", reason="keyboard_interrupt")
        except asyncio.CancelledError:
            logger.info("http_server_shutdown", reason="cancelled")
        except Exception as e:
            logger.critical(
                "fatal_server_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            sys.exit(1)

    def shutdown(self) -> None:
        """Graceful shutdown of all components.

        Stops:
        - Background workers
        - Discovery orchestrator
        - All providers

        This method is safe to call multiple times.
        """
        if self._shutdown_requested:
            logger.debug("shutdown_already_requested")
            return

        self._shutdown_requested = True
        logger.info("server_lifecycle_shutdown_start")

        self._context.shutdown()
        self._running = False

        logger.info("server_lifecycle_shutdown_complete")


def _setup_signal_handlers(lifecycle: ServerLifecycle) -> None:
    """Setup graceful shutdown on SIGTERM/SIGINT.

    Args:
        lifecycle: ServerLifecycle instance to shutdown on signal.
    """

    def handler(signum, frame):
        sig_name = signal.Signals(signum).name
        logger.info("shutdown_signal_received", signal=sig_name)
        lifecycle.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGTERM, handler)
    signal.signal(signal.SIGINT, handler)


def _setup_logging_from_config(cli_config: CLIConfig) -> None:
    """Setup logging based on CLI config and config file.

    Logging configuration priority:
    1. CLI arguments (--log-level, --log-file, --json-logs)
    2. Config file (logging section)
    3. Environment variables
    4. Defaults

    Args:
        cli_config: Parsed CLI configuration.
    """
    log_level = cli_config.log_level
    log_file = cli_config.log_file
    json_format = cli_config.json_logs

    # Try to load additional settings from config file
    if cli_config.config_path and Path(cli_config.config_path).exists():
        try:
            full_config = load_config_from_file(cli_config.config_path)
            logging_config = full_config.get("logging", {})

            # Config file values are used only if CLI didn't specify
            if cli_config.log_level == "INFO":  # Default value
                log_level = logging_config.get("level", log_level).upper()

            if not cli_config.log_file:
                log_file = logging_config.get("file", log_file)

            if not cli_config.json_logs:
                json_format = logging_config.get("json_format", json_format)

        except (FileNotFoundError, yaml.YAMLError, ValueError, OSError) as e:
            # Config loading failed - use CLI values, log will be set up shortly
            logger.debug("config_preload_failed", error=str(e))

    setup_logging(level=log_level, json_format=json_format, log_file=log_file)


def run_server(cli_config: CLIConfig) -> None:
    """Main entry point that ties everything together.

    This function orchestrates:
    1. Setup logging based on CLI config
    2. Bootstrap application
    3. Setup signal handlers
    4. Start lifecycle (background workers, discovery)
    5. Run appropriate server mode
    6. Handle shutdown on exit/signal

    Args:
        cli_config: Parsed CLI configuration from parse_args().
    """
    # Setup logging first
    _setup_logging_from_config(cli_config)

    mode_str = "http" if cli_config.http_mode else "stdio"
    logger.info(
        "mcp_registry_starting",
        mode=mode_str,
        log_file=cli_config.log_file,
    )

    # Bootstrap application
    context = bootstrap(cli_config.config_path)

    # Create lifecycle manager
    lifecycle = ServerLifecycle(context)

    # Setup signal handlers for graceful shutdown
    _setup_signal_handlers(lifecycle)

    # Start background components
    lifecycle.start()

    # Log ready state
    provider_ids = list(context.runtime.repository.get_all_ids())
    orchestrator = get_discovery_orchestrator()
    discovery_status = "enabled" if orchestrator else "disabled"

    logger.info(
        "mcp_registry_ready",
        providers=provider_ids,
        discovery=discovery_status,
    )

    # Run server in appropriate mode
    try:
        if cli_config.http_mode:
            lifecycle.run_http(cli_config.http_host, cli_config.http_port)
        else:
            lifecycle.run_stdio()
    finally:
        # Ensure cleanup on exit
        lifecycle.shutdown()


__all__ = [
    "ServerLifecycle",
    "run_server",
]
