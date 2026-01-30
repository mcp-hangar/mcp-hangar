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
from .cli_legacy import CLIConfig
from .config import load_config_from_file
from .state import get_discovery_orchestrator, get_runtime_providers

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

        # Get the MCP app from FastMCP
        mcp_app = mcp_server.streamable_http_app()

        # Create auxiliary routes for /metrics, /health, /ready
        import time

        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, PlainTextResponse
        from starlette.routing import Route

        from ..metrics import get_metrics
        from ..server.state import PROVIDERS

        _start_time = time.time()
        _startup_complete = False

        def liveness_endpoint(request):
            """Liveness check - is the process alive?"""
            return JSONResponse({"status": "healthy"})

        def readiness_endpoint(request):
            """Readiness check - can we handle traffic?"""
            ready_count = sum(1 for p in PROVIDERS.values() if p.state.value == "ready")
            total_count = len(PROVIDERS)
            is_ready = ready_count > 0 or total_count == 0
            return JSONResponse(
                {
                    "status": "healthy" if is_ready else "unhealthy",
                    "ready_providers": ready_count,
                    "total_providers": total_count,
                },
                status_code=200 if is_ready else 503,
            )

        def startup_endpoint(request):
            """Startup check - has initialization completed?"""
            nonlocal _startup_complete
            # Mark startup complete after first check (bootstrap is done by this point)
            _startup_complete = True
            uptime = time.time() - _start_time
            return JSONResponse(
                {
                    "status": "healthy",
                    "startup_complete": _startup_complete,
                    "uptime_seconds": round(uptime, 2),
                }
            )

        def metrics_endpoint(request):
            """Prometheus metrics endpoint."""
            return PlainTextResponse(
                get_metrics(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        routes = [
            Route("/health/live", liveness_endpoint, methods=["GET"]),
            Route("/health/ready", readiness_endpoint, methods=["GET"]),
            Route("/health/startup", startup_endpoint, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
        ]

        aux_app = Starlette(routes=routes)

        async def combined_app(scope, receive, send):
            """Combined ASGI app that routes to metrics/health or MCP."""
            if scope["type"] == "http":
                path = scope.get("path", "")
                if path.startswith("/health/") or path == "/metrics":
                    await aux_app(scope, receive, send)
                    return
            await mcp_app(scope, receive, send)

        # Apply authentication middleware if enabled
        auth_components = self._context.auth_components
        if auth_components and auth_components.enabled:
            starlette_app = self._create_auth_app(combined_app, auth_components)
            logger.info("http_auth_enabled")
        else:
            starlette_app = combined_app

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
        - Runtime (hot-loaded) providers
        - Background workers
        - Discovery orchestrator
        - All configured providers

        This method is safe to call multiple times.
        """
        if self._shutdown_requested:
            logger.debug("shutdown_already_requested")
            return

        self._shutdown_requested = True
        logger.info("server_lifecycle_shutdown_start")

        self._cleanup_runtime_providers()

        self._context.shutdown()
        self._running = False

        logger.info("server_lifecycle_shutdown_complete")

    def _cleanup_runtime_providers(self) -> None:
        """Cleanup all hot-loaded runtime providers."""
        runtime_store = get_runtime_providers()
        if runtime_store.count() == 0:
            return

        logger.info(
            "cleaning_up_runtime_providers",
            count=runtime_store.count(),
        )

        for provider, metadata in runtime_store.list_all():
            try:
                provider.shutdown()
            except Exception as e:
                logger.warning(
                    "runtime_provider_shutdown_error",
                    provider_id=str(provider.provider_id),
                    error=str(e),
                )

            if metadata.cleanup:
                try:
                    metadata.cleanup()
                except Exception as e:
                    logger.warning(
                        "runtime_provider_cleanup_error",
                        provider_id=str(provider.provider_id),
                        error=str(e),
                    )

        runtime_store.clear()
        logger.info("runtime_providers_cleaned_up")

    def _create_auth_app(self, inner_app, auth_components):
        """Create auth-enabled ASGI app wrapper.

        Args:
            inner_app: The inner ASGI app to wrap.
            auth_components: Auth components with middleware.

        Returns:
            ASGI app with authentication.
        """
        from starlette.responses import JSONResponse

        from ..domain.contracts.authentication import AuthRequest
        from ..domain.exceptions import AccessDeniedError, AuthenticationError

        # Paths to skip authentication (health checks, metrics)
        skip_paths = frozenset(["/health/live", "/health/ready", "/health/startup", "/metrics"])
        # Default trusted proxies (should be configured in production)
        trusted_proxies = frozenset(["127.0.0.1", "::1"])

        async def auth_app(scope, receive, send):
            """ASGI app with authentication middleware."""
            if scope["type"] != "http":
                await inner_app(scope, receive, send)
                return

            path = scope.get("path", "")

            # Skip auth for health/metrics endpoints
            if path in skip_paths or path.startswith("/health/"):
                await inner_app(scope, receive, send)
                return

            # Build headers dict from scope
            headers = {}
            for key, value in scope.get("headers", []):
                headers[key.decode("latin-1").lower()] = value.decode("latin-1")

            # Get client IP
            client = scope.get("client")
            source_ip = client[0] if client else "unknown"

            # Trust X-Forwarded-For only from trusted proxies
            if source_ip in trusted_proxies:
                forwarded_for = headers.get("x-forwarded-for")
                if forwarded_for:
                    source_ip = forwarded_for.split(",")[0].strip()

            # Create auth request
            auth_request = AuthRequest(
                headers=headers,
                source_ip=source_ip,
                method=scope.get("method", ""),
                path=path,
            )

            try:
                # Authenticate
                auth_context = auth_components.authn_middleware.authenticate(auth_request)

                # Store auth context in scope for downstream handlers
                scope["auth"] = auth_context

                # Pass to inner app
                await inner_app(scope, receive, send)

            except AuthenticationError as e:
                response = JSONResponse(
                    status_code=401,
                    content={
                        "error": "authentication_failed",
                        "message": e.message,
                    },
                    headers={"WWW-Authenticate": "Bearer, ApiKey"},
                )
                await response(scope, receive, send)

            except AccessDeniedError as e:
                response = JSONResponse(
                    status_code=403,
                    content={
                        "error": "access_denied",
                        "message": str(e),
                    },
                )
                await response(scope, receive, send)

        return auth_app


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
