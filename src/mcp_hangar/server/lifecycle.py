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
import ipaddress
from pathlib import Path
import signal
import sys
import threading
from typing import Any

import yaml

from ..logging_config import get_logger, setup_logging
from .api.middleware import create_auth_enforced_app
from .bootstrap import ApplicationContext, bootstrap
from .cli.cli_compat import CLIConfig
from .config import load_config_from_file
from .state import get_discovery_orchestrator, get_runtime_mcp_servers

logger = get_logger(__name__)


def _is_loopback_host(host: str) -> bool:
    """Return whether a bind host resolves to loopback-only."""
    normalized_host = host.strip().lower()
    if normalized_host in {"127.0.0.1", "::1", "localhost"}:
        return True

    try:
        return ipaddress.ip_address(normalized_host).is_loopback
    except ValueError:
        return False


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
        self._discovery_loop: asyncio.AbstractEventLoop | None = None
        self._discovery_thread: threading.Thread | None = None

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

        self._start_discovery()

    def _start_discovery(self) -> None:
        """Start discovery on a dedicated long-lived event loop."""
        orchestrator = self._context.discovery_orchestrator
        if orchestrator is None:
            return

        ready = threading.Event()

        def run_loop() -> None:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            self._discovery_loop = loop
            ready.set()
            loop.run_forever()
            loop.close()

        self._discovery_thread = threading.Thread(target=run_loop, name="mcp-hangar-discovery", daemon=True)
        self._discovery_thread.start()
        ready.wait()

        assert self._discovery_loop is not None
        try:
            asyncio.run_coroutine_threadsafe(orchestrator.start(), self._discovery_loop).result()
        except Exception:
            self._discovery_loop.call_soon_threadsafe(self._discovery_loop.stop)
            self._discovery_thread.join()
            self._discovery_loop = None
            self._discovery_thread = None
            raise

        logger.info("discovery_started", sources_count=orchestrator.get_stats()["sources_count"])

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
        except Exception as e:  # noqa: BLE001 -- fault-barrier: fatal server error boundary
            logger.critical(
                "fatal_server_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            sys.exit(1)

    def run_http(self, host: str, port: int, unsafe_no_auth: bool = False) -> None:
        """Run MCP server in HTTP mode. Blocks until exit.

        This mode is compatible with LM Studio and other MCP HTTP clients.

        Endpoints:
        - /mcp: Streamable HTTP MCP endpoint (POST/GET)

        Args:
            host: Host to bind to.
            port: Port to bind to.
        """
        import uvicorn

        auth_components = self._context.auth_components
        auth_enabled = bool(auth_components and auth_components.enabled)
        if not auth_enabled and not _is_loopback_host(host):
            message = "Refusing to start HTTP on non-loopback without authentication. Use --unsafe-no-auth to override."
            if not unsafe_no_auth:
                logger.error("http_auth_required_for_non_loopback", host=host, port=port, message=message)
                raise SystemExit(1)

            logger.warning(
                "http_auth_disabled_non_loopback_override",
                host=host,
                port=port,
                message=message,
            )

        logger.info("starting_http_server", host=host, port=port)

        # Update FastMCP settings for HTTP mode. FastMCP (SDK v1) carries
        # host/port on .settings; MCPServer (SDK v2) has no .settings, and the
        # host uvicorn below binds host/port directly, so this is a no-op there.
        mcp_server = self._context.mcp_server
        if hasattr(mcp_server, "settings"):
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
        from .bootstrap.composition import get_runtime

        _start_time = time.time()
        _startup_complete = False

        def liveness_endpoint(request):
            """Liveness check - is the process alive?"""
            return JSONResponse({"status": "healthy"})

        from ..observability.health import get_event_store_durability_status

        def readiness_endpoint(request):
            """Readiness check - can we handle traffic?"""
            repository = get_runtime().repository
            ready_count = sum(1 for p in repository.get_all().values() if p.state.value == "ready")
            total_count = repository.count()
            is_ready = ready_count > 0 or total_count == 0

            # Fail readiness when the event store silently degraded to a
            # non-durable in-memory store while a durable driver was configured.
            durability = get_event_store_durability_status()
            event_store_ok = durability is None or not durability.degraded

            body: dict[str, Any] = {
                "status": "healthy" if (is_ready and event_store_ok) else "unhealthy",
                "ready_mcp_servers": ready_count,
                "total_mcp_servers": total_count,
            }
            if not event_store_ok and durability is not None:
                body["event_store"] = {
                    "status": "unhealthy",
                    "configured_driver": durability.configured_driver,
                    "durable": durability.durable,
                    "detail": durability.detail,
                }
            return JSONResponse(
                body,
                status_code=200 if (is_ready and event_store_ok) else 503,
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

        # Register RFC 9728 Protected Resource Metadata endpoint (unauthenticated discovery).
        # The endpoint is placed on the aux app (outside auth enforcement) so clients can
        # reach it before obtaining a token.  When OIDC is not configured / no issuer is
        # set, we return 404 — there is nothing to advertise.
        _oidc_issuers: list[str] = (
            auth_components.oidc_issuers if auth_components and hasattr(auth_components, "oidc_issuers") else []
        )
        _oidc_resource_uri_cfg = (
            auth_components.oidc_resource_uri
            if auth_components and hasattr(auth_components, "oidc_resource_uri")
            else ""
        )

        from ..auth.prm import build_prm_response, build_resource_base_url

        def prm_endpoint(request):
            """RFC 9728 Protected Resource Metadata (unauthenticated discovery).

            Returns 404 when no OIDC issuer is configured — nothing to advertise.
            """
            if not _oidc_issuers:
                return JSONResponse(
                    {"error": "not_found", "message": "No OIDC issuer configured"},
                    status_code=404,
                )
            resource_base = _oidc_resource_uri_cfg or build_resource_base_url(request.scope)
            return JSONResponse(
                build_prm_response(issuers=_oidc_issuers, resource_uri=resource_base),
                media_type="application/json",
            )

        routes.append(Route("/.well-known/oauth-protected-resource", prm_endpoint, methods=["GET"]))

        # Create REST API router for /api/* endpoints
        # Stdio mode: pass auth_components from context for consistency.
        # Auth enforcement on REST API applies even in stdio mode when auth is configured.
        from ..server.api import create_api_router

        api_app = create_api_router(auth_components=getattr(self._context, "auth_components", None))

        # Wire enterprise approval service into Starlette app state.
        approval_svc = getattr(self._context, "approval_service", None)
        if approval_svc is not None:
            api_app.state.approval_gate_service = approval_svc

        # Mount health/metrics and REST API together in one Starlette app
        from starlette.routing import Mount

        all_routes = routes + [Mount("/api", app=api_app)]
        aux_app = Starlette(routes=all_routes)

        _PRM_PATH = "/.well-known/oauth-protected-resource"

        async def combined_app(scope, receive, send):
            """Combined ASGI app that routes to aux (health/metrics/api) or MCP."""
            if scope["type"] in ("http", "websocket"):
                path = scope.get("path", "")
                if (
                    path.startswith("/health/")
                    or path == "/metrics"
                    or path == _PRM_PATH
                    or path == "/api"
                    or path.startswith("/api/")
                ):
                    await aux_app(scope, receive, send)
                    return
            await mcp_app(scope, receive, send)

        # Apply authentication middleware if enabled
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
            try:
                await server.serve()
            finally:
                self.shutdown()
                logger.info("http_server_stopped")

        try:
            asyncio.run(run_server())
        except KeyboardInterrupt:
            logger.info("http_server_shutdown", reason="keyboard_interrupt")
        except asyncio.CancelledError:
            logger.info("http_server_shutdown", reason="cancelled")
        except Exception as e:  # noqa: BLE001 -- fault-barrier: fatal server error boundary
            logger.critical(
                "fatal_server_error",
                error=str(e),
                error_type=type(e).__name__,
            )
            sys.exit(1)

    def shutdown(self) -> None:
        """Graceful shutdown of all components.

        Stops:
        - Runtime (hot-loaded) mcp_servers
        - Background workers
        - Discovery orchestrator
        - All configured mcp_servers

        This method is safe to call multiple times.
        """
        if self._shutdown_requested:
            logger.debug("shutdown_already_requested")
            return

        self._shutdown_requested = True
        logger.info("server_lifecycle_shutdown_start")

        self._cleanup_runtime_mcp_servers()

        self._stop_discovery()
        self._context.shutdown()
        self._running = False

        logger.info("server_lifecycle_shutdown_complete")

    def _stop_discovery(self) -> None:
        """Await discovery cleanup, then stop and join its dedicated loop."""
        orchestrator = self._context.discovery_orchestrator
        loop = self._discovery_loop
        thread = self._discovery_thread
        if orchestrator is None or loop is None or thread is None:
            return

        try:
            asyncio.run_coroutine_threadsafe(orchestrator.stop(), loop).result()
        except Exception as e:  # noqa: BLE001 -- shutdown must continue after discovery cleanup failure
            logger.warning("discovery_orchestrator_stop_failed", error=str(e))
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join()
            self._discovery_loop = None
            self._discovery_thread = None

    def _cleanup_runtime_mcp_servers(self) -> None:
        """Cleanup all hot-loaded runtime mcp_servers."""
        runtime_store = get_runtime_mcp_servers()
        if runtime_store.count() == 0:
            return

        logger.info(
            "cleaning_up_runtime_mcp_servers",
            count=runtime_store.count(),
        )

        for mcp_server, metadata in runtime_store.list_all():
            try:
                mcp_server.shutdown()
            except Exception as e:  # noqa: BLE001 -- fault-barrier: one mcp_server shutdown failure must not prevent others
                logger.warning(
                    "runtime_mcp_server_shutdown_error",
                    mcp_server_id=str(mcp_server.mcp_server_id),
                    error=str(e),
                )

            if metadata.cleanup:
                try:
                    metadata.cleanup()
                except Exception as e:  # noqa: BLE001 -- fault-barrier: cleanup callback failure must not prevent other cleanups
                    logger.warning(
                        "runtime_mcp_server_cleanup_error",
                        mcp_server_id=str(mcp_server.mcp_server_id),
                        error=str(e),
                    )

        runtime_store.clear()
        logger.info("runtime_mcp_servers_cleaned_up")

    def _create_auth_app(self, inner_app, auth_components):
        """Create auth-enabled ASGI app wrapper.

        Args:
            inner_app: The inner ASGI app to wrap.
            auth_components: Auth components with middleware.

        Returns:
            ASGI app with authentication.
        """
        return create_auth_enforced_app(inner_app, auth_components)


def _setup_signal_handlers(lifecycle: ServerLifecycle) -> None:
    """Setup graceful shutdown on SIGTERM/SIGINT and reload on SIGHUP.

    Args:
        lifecycle: ServerLifecycle instance to shutdown on signal.
    """

    def shutdown_handler(signum, _frame):
        sig_name = signal.Signals(signum).name
        logger.info("shutdown_signal_received", signal=sig_name)
        lifecycle.shutdown()
        sys.exit(0)

    def reload_handler(signum, _frame):
        """Handle SIGHUP for configuration reload."""
        sig_name = signal.Signals(signum).name
        logger.info("reload_signal_received", signal=sig_name)

        try:
            from ..application.commands.commands import ReloadConfigurationCommand

            command = ReloadConfigurationCommand(
                graceful=True,
                requested_by="sighup",
            )

            # Access command bus from lifecycle context
            result = lifecycle._context.runtime.command_bus.send(command)
            logger.info("config_reload_completed_via_signal", result=result)

        except Exception as e:  # noqa: BLE001 -- fault-barrier: signal handler must not crash process
            logger.error(
                "config_reload_failed_via_signal",
                error=str(e),
                error_type=type(e).__name__,
            )

    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

    # SIGHUP is not available on Windows
    if hasattr(signal, "SIGHUP"):
        signal.signal(signal.SIGHUP, reload_handler)
        logger.debug("sighup_handler_registered")


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
    _setup_signal_handlers(lifecycle)

    # Start background components and the dedicated discovery lifecycle loop.
    lifecycle.start()

    # Log ready state
    mcp_server_ids = list(context.runtime.repository.get_all_ids())
    orchestrator = get_discovery_orchestrator()
    discovery_status = "enabled" if orchestrator else "disabled"

    logger.info(
        "mcp_registry_ready",
        mcp_servers=mcp_server_ids,
        discovery=discovery_status,
    )

    # Run server in appropriate mode
    try:
        if cli_config.http_mode:
            lifecycle.run_http(cli_config.http_host, cli_config.http_port, unsafe_no_auth=cli_config.unsafe_no_auth)
        else:
            lifecycle.run_stdio()
    finally:
        # Ensure cleanup on exit
        lifecycle.shutdown()


__all__ = [
    "ServerLifecycle",
    "run_server",
]
