"""MCP HTTP Server using FastMCP.

Provides MCP-over-HTTP with proper dependency injection.
No global state — all dependencies passed via constructor.

Endpoints (HTTP mode):
- /health : liveness (cheap ping)
- /ready  : readiness (checks internal registry wiring + basic runtime state)
- /metrics: prometheus metrics
- /mcp    : MCP streamable HTTP endpoint

Usage:
    # Recommended: Use MCPServerFactory
    from mcp_hangar.fastmcp_server import MCPServerFactory, RegistryFunctions

    registry = RegistryFunctions(
        list=my_list_fn,
        start=my_start_fn,
        stop=my_stop_fn,
        invoke=my_invoke_fn,
        tools=my_tools_fn,
        details=my_details_fn,
        health=my_health_fn,
    )

    factory = MCPServerFactory(registry)
    app = factory.create_asgi_app()

    # Or use the builder pattern:
    factory = (MCPServerFactory.builder()
        .with_registry(list_fn, start_fn, stop_fn, invoke_fn, tools_fn, details_fn, health_fn)
        .with_discovery(discover_fn=discover_fn)
        .with_config(port=9000)
        .build())
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP

from .logging_config import get_logger

if TYPE_CHECKING:
    from .server.auth_bootstrap import AuthComponents

logger = get_logger(__name__)


# =============================================================================
# Protocols for Type Safety
# =============================================================================


class RegistryListFn(Protocol):
    """Protocol for registry_list function."""

    def __call__(self, state_filter: Optional[str] = None) -> Dict[str, Any]:
        """List all providers with status and metadata."""
        ...


class RegistryStartFn(Protocol):
    """Protocol for registry_start function."""

    def __call__(self, provider: str) -> Dict[str, Any]:
        """Start a provider and discover tools."""
        ...


class RegistryStopFn(Protocol):
    """Protocol for registry_stop function."""

    def __call__(self, provider: str) -> Dict[str, Any]:
        """Stop a provider."""
        ...


class RegistryInvokeFn(Protocol):
    """Protocol for registry_invoke function."""

    def __call__(
        self,
        provider: str,
        tool: str,
        arguments: Dict[str, Any],
        timeout: float = 30.0,
    ) -> Dict[str, Any]:
        """Invoke a tool on a provider."""
        ...


class RegistryToolsFn(Protocol):
    """Protocol for registry_tools function."""

    def __call__(self, provider: str) -> Dict[str, Any]:
        """Get tool schemas for a provider."""
        ...


class RegistryDetailsFn(Protocol):
    """Protocol for registry_details function."""

    def __call__(self, provider: str) -> Dict[str, Any]:
        """Get detailed provider information."""
        ...


class RegistryHealthFn(Protocol):
    """Protocol for registry_health function."""

    def __call__(self) -> Dict[str, Any]:
        """Get registry health status."""
        ...


# Discovery protocols (optional)


class RegistryDiscoverFn(Protocol):
    """Protocol for registry_discover function (async)."""

    async def __call__(self) -> Dict[str, Any]:
        """Trigger immediate discovery cycle."""
        ...


class RegistryDiscoveredFn(Protocol):
    """Protocol for registry_discovered function."""

    def __call__(self) -> Dict[str, Any]:
        """List discovered providers pending registration."""
        ...


class RegistryQuarantineFn(Protocol):
    """Protocol for registry_quarantine function."""

    def __call__(self) -> Dict[str, Any]:
        """List quarantined providers."""
        ...


class RegistryApproveFn(Protocol):
    """Protocol for registry_approve function (async)."""

    async def __call__(self, provider: str) -> Dict[str, Any]:
        """Approve a quarantined provider."""
        ...


class RegistrySourcesFn(Protocol):
    """Protocol for registry_sources function."""

    def __call__(self) -> Dict[str, Any]:
        """List discovery sources with status."""
        ...


class RegistryMetricsFn(Protocol):
    """Protocol for registry_metrics function."""

    def __call__(self, format: str = "summary") -> Dict[str, Any]:
        """Get registry metrics."""
        ...


# =============================================================================
# Configuration
# =============================================================================


@dataclass(frozen=True)
class RegistryFunctions:
    """Container for all registry function dependencies.

    Core functions are required. Discovery functions are optional
    and will return appropriate errors if not provided.

    Attributes:
        list: Function to list all providers.
        start: Function to start a provider.
        stop: Function to stop a provider.
        invoke: Function to invoke a tool on a provider.
        tools: Function to get tool schemas.
        details: Function to get provider details.
        health: Function to get registry health.
        discover: Optional async function to trigger discovery.
        discovered: Optional function to list discovered providers.
        quarantine: Optional function to list quarantined providers.
        approve: Optional async function to approve a quarantined provider.
        sources: Optional function to list discovery sources.
        metrics: Optional function to get registry metrics.
    """

    # Core (required)
    list: RegistryListFn
    start: RegistryStartFn
    stop: RegistryStopFn
    invoke: RegistryInvokeFn
    tools: RegistryToolsFn
    details: RegistryDetailsFn
    health: RegistryHealthFn

    # Discovery (optional)
    discover: Optional[RegistryDiscoverFn] = None
    discovered: Optional[RegistryDiscoveredFn] = None
    quarantine: Optional[RegistryQuarantineFn] = None
    approve: Optional[RegistryApproveFn] = None
    sources: Optional[RegistrySourcesFn] = None
    metrics: Optional[RegistryMetricsFn] = None


@dataclass(frozen=True)
class ServerConfig:
    """HTTP server configuration.

    Attributes:
        host: Host to bind to.
        port: Port to bind to.
        streamable_http_path: Path for MCP streamable HTTP endpoint.
        sse_path: Path for SSE endpoint.
        message_path: Path for message endpoint.
        auth_enabled: Whether authentication is enabled (opt-in, default False).
        auth_skip_paths: Paths to skip authentication (health, metrics, etc.).
        trusted_proxies: Set of trusted proxy IPs for X-Forwarded-For.
    """

    host: str = "0.0.0.0"
    port: int = 8000
    streamable_http_path: str = "/mcp"
    sse_path: str = "/sse"
    message_path: str = "/messages/"
    # Auth configuration (opt-in)
    auth_enabled: bool = False
    auth_skip_paths: tuple[str, ...] = ("/health", "/ready", "/_ready", "/metrics")
    trusted_proxies: frozenset[str] = frozenset(["127.0.0.1", "::1"])


# =============================================================================
# Factory
# =============================================================================


class MCPServerFactory:
    """Factory for creating configured FastMCP servers.

    This factory encapsulates all dependencies needed to create an MCP server,
    enabling proper dependency injection and testability.

    Usage:
        # Direct instantiation
        factory = MCPServerFactory(registry_functions)
        mcp = factory.create_server()
        app = factory.create_asgi_app()

        # With authentication (opt-in)
        factory = MCPServerFactory(
            registry_functions,
            auth_components=auth_components,
            config=ServerConfig(auth_enabled=True),
        )
        app = factory.create_asgi_app()

        # Or use the builder pattern
        factory = (MCPServerFactory.builder()
            .with_registry(list_fn, start_fn, ...)
            .with_discovery(discover_fn, ...)
            .with_auth(auth_components)
            .with_config(host="0.0.0.0", port=9000, auth_enabled=True)
            .build())
    """

    def __init__(
        self,
        registry: RegistryFunctions,
        config: Optional[ServerConfig] = None,
        auth_components: Optional["AuthComponents"] = None,
    ):
        """Initialize factory with dependencies.

        Args:
            registry: Registry function implementations.
            config: Server configuration (uses defaults if None).
            auth_components: Optional auth components for authentication/authorization.
        """
        self._registry = registry
        self._config = config or ServerConfig()
        self._auth_components = auth_components
        self._mcp: Optional[FastMCP] = None

    @classmethod
    def builder(cls) -> "MCPServerFactoryBuilder":
        """Create a builder for fluent configuration.

        Returns:
            MCPServerFactoryBuilder instance.
        """
        return MCPServerFactoryBuilder()

    @property
    def registry(self) -> RegistryFunctions:
        """Get the registry functions."""
        return self._registry

    @property
    def config(self) -> ServerConfig:
        """Get the server configuration."""
        return self._config

    def create_server(self) -> FastMCP:
        """Create and configure FastMCP server instance.

        The server is cached — repeated calls return the same instance.

        Returns:
            Configured FastMCP server with all tools registered.
        """
        if self._mcp is not None:
            return self._mcp

        mcp = FastMCP(
            name="mcp-registry",
            host=self._config.host,
            port=self._config.port,
            streamable_http_path=self._config.streamable_http_path,
            sse_path=self._config.sse_path,
            message_path=self._config.message_path,
        )

        self._register_core_tools(mcp)
        self._register_discovery_tools(mcp)

        self._mcp = mcp
        logger.info(
            "fastmcp_server_created",
            host=self._config.host,
            port=self._config.port,
            discovery_enabled=self._registry.discover is not None,
        )

        return mcp

    def create_asgi_app(self):
        """Create ASGI application with metrics/health endpoints.

        Creates a combined ASGI app that handles:
        - /health: Liveness endpoint
        - /ready: Readiness endpoint with internal checks
        - /metrics: Prometheus metrics
        - /mcp: MCP streamable HTTP endpoint (and related paths)

        If auth is enabled (config.auth_enabled=True and auth_components provided),
        the auth middleware will be applied to protect MCP endpoints.

        Returns:
            Combined ASGI app callable.
        """
        from starlette.applications import Starlette
        from starlette.responses import JSONResponse, PlainTextResponse
        from starlette.routing import Route

        from .metrics import get_metrics

        mcp = self.create_server()
        mcp_app = mcp.streamable_http_app()

        # Log if auth is configured (actual wrapping happens in _create_auth_combined_app)
        if self._config.auth_enabled and self._auth_components:

            logger.info(
                "auth_middleware_enabled",
                skip_paths=self._config.auth_skip_paths,
                trusted_proxies=list(self._config.trusted_proxies),
            )

        # Health endpoint (liveness)
        async def health_endpoint(request):
            """Liveness endpoint (cheap ping)."""
            return JSONResponse({"status": "ok", "service": "mcp-registry"})

        # Readiness endpoint
        async def ready_endpoint(request):
            """Readiness endpoint with internal checks."""
            checks = self._run_readiness_checks()
            ready = all(v is True for k, v in checks.items() if isinstance(v, bool))
            return JSONResponse(
                {"ready": ready, "service": "mcp-registry", "checks": checks},
                status_code=200 if ready else 503,
            )

        # Metrics endpoint
        async def metrics_endpoint(request):
            """Prometheus metrics endpoint."""
            self._update_metrics()
            return PlainTextResponse(
                get_metrics(),
                media_type="text/plain; version=0.0.4; charset=utf-8",
            )

        routes = [
            Route("/health", health_endpoint, methods=["GET"]),
            Route("/ready", ready_endpoint, methods=["GET"]),
            Route("/metrics", metrics_endpoint, methods=["GET"]),
        ]

        aux_app = Starlette(routes=routes)

        # Create auth-aware combined app
        if self._config.auth_enabled and self._auth_components:
            combined_app = self._create_auth_combined_app(aux_app, mcp_app)
        else:

            async def combined_app(scope, receive, send):
                """Combined ASGI app that routes to metrics/health or MCP."""
                if scope["type"] == "http":
                    path = scope.get("path", "")
                    if path in ("/health", "/ready", "/metrics"):
                        await aux_app(scope, receive, send)
                        return
                await mcp_app(scope, receive, send)

        return combined_app

    def _create_auth_combined_app(self, aux_app, mcp_app):
        """Create auth-enabled combined ASGI app.

        This wraps the MCP app with authentication middleware while
        keeping health/metrics endpoints unprotected.

        Args:
            aux_app: Starlette app for health/metrics endpoints.
            mcp_app: FastMCP ASGI app.

        Returns:
            Combined ASGI app with auth middleware.
        """
        from starlette.responses import JSONResponse

        from .domain.contracts.authentication import AuthRequest
        from .domain.exceptions import AccessDeniedError, AuthenticationError

        auth_components = self._auth_components
        skip_paths = set(self._config.auth_skip_paths)
        trusted_proxies = self._config.trusted_proxies

        async def auth_combined_app(scope, receive, send):
            """Combined ASGI app with authentication for MCP endpoints."""
            if scope["type"] != "http":
                # Non-HTTP (e.g., lifespan) - pass through
                await mcp_app(scope, receive, send)
                return

            path = scope.get("path", "")

            # Skip auth for health/metrics endpoints
            if path in skip_paths:
                await aux_app(scope, receive, send)
                return

            # For MCP endpoints, apply authentication
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

                # Pass to MCP app
                await mcp_app(scope, receive, send)

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

        return auth_combined_app

    def _register_core_tools(self, mcp: FastMCP) -> None:
        """Register core registry tools.

        Args:
            mcp: FastMCP server instance.
        """
        reg = self._registry

        @mcp.tool()
        def registry_list(state_filter: str = None) -> dict:
            """List all providers with status and metadata.

            Args:
                state_filter: Optional filter by state (cold, ready, degraded, dead)
            """
            return reg.list(state_filter=state_filter)

        @mcp.tool()
        def registry_start(provider: str) -> dict:
            """Explicitly start a provider and discover tools.

            Args:
                provider: Provider ID to start
            """
            return reg.start(provider=provider)

        @mcp.tool()
        def registry_stop(provider: str) -> dict:
            """Stop a provider.

            Args:
                provider: Provider ID to stop
            """
            return reg.stop(provider=provider)

        @mcp.tool()
        def registry_invoke(
            provider: str,
            tool: str,
            arguments: Optional[dict] = None,
            timeout: float = 30.0,
        ) -> dict:
            """Invoke a tool on a provider.

            Args:
                provider: Provider ID
                tool: Tool name to invoke
                arguments: Tool arguments as dictionary (default: empty)
                timeout: Timeout in seconds (default 30)
            """
            return reg.invoke(
                provider=provider,
                tool=tool,
                arguments=arguments or {},
                timeout=timeout,
            )

        @mcp.tool()
        def registry_tools(provider: str) -> dict:
            """Get detailed tool schemas for a provider.

            Args:
                provider: Provider ID
            """
            return reg.tools(provider=provider)

        @mcp.tool()
        def registry_details(provider: str) -> dict:
            """Get detailed information about a provider.

            Args:
                provider: Provider ID
            """
            return reg.details(provider=provider)

        @mcp.tool()
        def registry_health() -> dict:
            """Get registry health status including provider counts and metrics."""
            return reg.health()

    def _register_discovery_tools(self, mcp: FastMCP) -> None:
        """Register discovery tools (if enabled).

        Args:
            mcp: FastMCP server instance.
        """
        reg = self._registry

        @mcp.tool()
        async def registry_discover() -> dict:
            """Trigger immediate discovery cycle.

            Runs discovery across all configured sources and returns
            statistics about discovered, registered, and quarantined providers.
            """
            if reg.discover is None:
                return {"error": "Discovery not configured"}
            return await reg.discover()

        @mcp.tool()
        def registry_discovered() -> dict:
            """List all discovered providers pending registration.

            Shows providers found by discovery but not yet registered,
            typically due to auto_register=false or pending approval.
            """
            if reg.discovered is None:
                return {"error": "Discovery not configured"}
            return reg.discovered()

        @mcp.tool()
        def registry_quarantine() -> dict:
            """List quarantined providers with failure reasons.

            Shows providers that failed validation and are waiting
            for manual approval or rejection.
            """
            if reg.quarantine is None:
                return {"error": "Discovery not configured"}
            return reg.quarantine()

        @mcp.tool()
        async def registry_approve(provider: str) -> dict:
            """Approve a quarantined provider for registration.

            Args:
                provider: Name of the quarantined provider to approve
            """
            if reg.approve is None:
                return {"error": "Discovery not configured"}
            return await reg.approve(provider=provider)

        @mcp.tool()
        def registry_sources() -> dict:
            """List configured discovery sources with health status.

            Shows all discovery sources (kubernetes, docker, filesystem, entrypoint)
            with their current health and last discovery timestamp.
            """
            if reg.sources is None:
                return {"error": "Discovery not configured"}
            return reg.sources()

        @mcp.tool()
        def registry_metrics(format: str = "summary") -> dict:
            """Get registry metrics and statistics.

            Args:
                format: Output format - "summary" (default), "prometheus", or "detailed"

            Returns metrics including provider states, tool call counts, errors,
            discovery statistics, and performance data.
            """
            if reg.metrics is None:
                return {"error": "Metrics not available"}
            return reg.metrics(format=format)

    def _run_readiness_checks(self) -> Dict[str, Any]:
        """Run readiness checks.

        Returns:
            Dictionary of check names to results.
        """
        checks: Dict[str, Any] = {}

        # Check registry wiring
        checks["registry_wired"] = True

        # Check registry list
        try:
            data = self._registry.list()
            checks["registry_list_ok"] = isinstance(data, dict) and "providers" in data
        except Exception as e:
            checks["registry_list_ok"] = False
            checks["registry_list_error"] = str(e)

        # Check registry health
        try:
            h = self._registry.health()
            checks["registry_health_ok"] = isinstance(h, dict) and "status" in h
        except Exception as e:
            checks["registry_health_ok"] = False
            checks["registry_health_error"] = str(e)

        return checks

    def _update_metrics(self) -> None:
        """Update provider state metrics."""
        from .metrics import update_provider_state

        try:
            data = self._registry.list()
            if isinstance(data, dict) and "providers" in data:
                for p in data.get("providers", []):
                    pid = p.get("provider_id") or p.get("name") or p.get("id")
                    if pid:
                        update_provider_state(
                            pid,
                            p.get("state", "cold"),
                            p.get("mode", "subprocess"),
                        )
        except Exception as e:
            logger.debug("metrics_update_failed", error=str(e))


# =============================================================================
# Builder (Optional Fluent API)
# =============================================================================


class MCPServerFactoryBuilder:
    """Builder for MCPServerFactory with fluent API.

    Provides a convenient way to construct an MCPServerFactory
    with optional components.

    Usage:
        factory = (MCPServerFactory.builder()
            .with_registry(list_fn, start_fn, stop_fn, invoke_fn, tools_fn, details_fn, health_fn)
            .with_discovery(discover_fn=my_discover)
            .with_config(port=9000)
            .build())
    """

    def __init__(self):
        """Initialize builder with empty state."""
        self._list_fn: Optional[RegistryListFn] = None
        self._start_fn: Optional[RegistryStartFn] = None
        self._stop_fn: Optional[RegistryStopFn] = None
        self._invoke_fn: Optional[RegistryInvokeFn] = None
        self._tools_fn: Optional[RegistryToolsFn] = None
        self._details_fn: Optional[RegistryDetailsFn] = None
        self._health_fn: Optional[RegistryHealthFn] = None

        self._discover_fn: Optional[RegistryDiscoverFn] = None
        self._discovered_fn: Optional[RegistryDiscoveredFn] = None
        self._quarantine_fn: Optional[RegistryQuarantineFn] = None
        self._approve_fn: Optional[RegistryApproveFn] = None
        self._sources_fn: Optional[RegistrySourcesFn] = None
        self._metrics_fn: Optional[RegistryMetricsFn] = None

        self._config: Optional[ServerConfig] = None
        self._auth_components: Optional["AuthComponents"] = None

    def with_registry(
        self,
        list_fn: RegistryListFn,
        start_fn: RegistryStartFn,
        stop_fn: RegistryStopFn,
        invoke_fn: RegistryInvokeFn,
        tools_fn: RegistryToolsFn,
        details_fn: RegistryDetailsFn,
        health_fn: RegistryHealthFn,
    ) -> "MCPServerFactoryBuilder":
        """Set core registry functions.

        Args:
            list_fn: Function to list providers.
            start_fn: Function to start a provider.
            stop_fn: Function to stop a provider.
            invoke_fn: Function to invoke a tool.
            tools_fn: Function to get tool schemas.
            details_fn: Function to get provider details.
            health_fn: Function to get registry health.

        Returns:
            Self for chaining.
        """
        self._list_fn = list_fn
        self._start_fn = start_fn
        self._stop_fn = stop_fn
        self._invoke_fn = invoke_fn
        self._tools_fn = tools_fn
        self._details_fn = details_fn
        self._health_fn = health_fn
        return self

    def with_discovery(
        self,
        discover_fn: Optional[RegistryDiscoverFn] = None,
        discovered_fn: Optional[RegistryDiscoveredFn] = None,
        quarantine_fn: Optional[RegistryQuarantineFn] = None,
        approve_fn: Optional[RegistryApproveFn] = None,
        sources_fn: Optional[RegistrySourcesFn] = None,
        metrics_fn: Optional[RegistryMetricsFn] = None,
    ) -> "MCPServerFactoryBuilder":
        """Set discovery functions (all optional).

        Args:
            discover_fn: Async function to trigger discovery.
            discovered_fn: Function to list discovered providers.
            quarantine_fn: Function to list quarantined providers.
            approve_fn: Async function to approve a provider.
            sources_fn: Function to list discovery sources.
            metrics_fn: Function to get metrics.

        Returns:
            Self for chaining.
        """
        self._discover_fn = discover_fn
        self._discovered_fn = discovered_fn
        self._quarantine_fn = quarantine_fn
        self._approve_fn = approve_fn
        self._sources_fn = sources_fn
        self._metrics_fn = metrics_fn
        return self

    def with_config(
        self,
        host: str = "0.0.0.0",
        port: int = 8000,
        streamable_http_path: str = "/mcp",
        sse_path: str = "/sse",
        message_path: str = "/messages/",
        auth_enabled: bool = False,
        auth_skip_paths: tuple[str, ...] = ("/health", "/ready", "/_ready", "/metrics"),
        trusted_proxies: frozenset[str] = frozenset(["127.0.0.1", "::1"]),
    ) -> "MCPServerFactoryBuilder":
        """Set server configuration.

        Args:
            host: Host to bind to.
            port: Port to bind to.
            streamable_http_path: Path for MCP streamable HTTP endpoint.
            sse_path: Path for SSE endpoint.
            message_path: Path for message endpoint.
            auth_enabled: Whether to enable authentication (default: False).
            auth_skip_paths: Paths to skip authentication.
            trusted_proxies: Trusted proxy IPs for X-Forwarded-For.

        Returns:
            Self for chaining.
        """
        self._config = ServerConfig(
            host=host,
            port=port,
            streamable_http_path=streamable_http_path,
            sse_path=sse_path,
            message_path=message_path,
            auth_enabled=auth_enabled,
            auth_skip_paths=auth_skip_paths,
            trusted_proxies=trusted_proxies,
        )
        return self

    def with_auth(
        self,
        auth_components: "AuthComponents",
    ) -> "MCPServerFactoryBuilder":
        """Set authentication components.

        Args:
            auth_components: Auth components from bootstrap_auth().

        Returns:
            Self for chaining.

        Note:
            You also need to set auth_enabled=True in with_config() for
            authentication to be active.
        """
        self._auth_components = auth_components
        return self

    def build(self) -> MCPServerFactory:
        """Build the factory.

        Returns:
            Configured MCPServerFactory instance.

        Raises:
            ValueError: If required registry functions not provided.
        """
        if not all(
            [
                self._list_fn,
                self._start_fn,
                self._stop_fn,
                self._invoke_fn,
                self._tools_fn,
                self._details_fn,
                self._health_fn,
            ]
        ):
            raise ValueError("All core registry functions must be provided via with_registry()")

        registry = RegistryFunctions(
            list=self._list_fn,
            start=self._start_fn,
            stop=self._stop_fn,
            invoke=self._invoke_fn,
            tools=self._tools_fn,
            details=self._details_fn,
            health=self._health_fn,
            discover=self._discover_fn,
            discovered=self._discovered_fn,
            quarantine=self._quarantine_fn,
            approve=self._approve_fn,
            sources=self._sources_fn,
            metrics=self._metrics_fn,
        )

        return MCPServerFactory(registry, self._config, self._auth_components)


# =============================================================================
# Backward Compatibility Layer
# DEPRECATED: Will be removed in v0.3.0
# =============================================================================

_compat_factory: Optional[MCPServerFactory] = None


def setup_fastmcp_server(
    registry_list_fn,
    registry_start_fn,
    registry_stop_fn,
    registry_tools_fn,
    registry_invoke_fn,
    registry_details_fn,
    registry_health_fn,
    # Discovery functions (optional)
    registry_discover_fn=None,
    registry_discovered_fn=None,
    registry_quarantine_fn=None,
    registry_approve_fn=None,
    registry_sources_fn=None,
    registry_metrics_fn=None,
):
    """DEPRECATED: Use MCPServerFactory instead.

    This function exists for backward compatibility only.
    Will be removed in v0.3.0.

    Args:
        registry_list_fn: Function to list providers.
        registry_start_fn: Function to start a provider.
        registry_stop_fn: Function to stop a provider.
        registry_tools_fn: Function to get tool schemas.
        registry_invoke_fn: Function to invoke a tool.
        registry_details_fn: Function to get provider details.
        registry_health_fn: Function to get registry health.
        registry_discover_fn: Optional async function to trigger discovery.
        registry_discovered_fn: Optional function to list discovered providers.
        registry_quarantine_fn: Optional function to list quarantined providers.
        registry_approve_fn: Optional async function to approve a provider.
        registry_sources_fn: Optional function to list discovery sources.
        registry_metrics_fn: Optional function to get metrics.
    """
    import warnings

    warnings.warn(
        "setup_fastmcp_server() is deprecated. Use MCPServerFactory instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    global _compat_factory

    registry = RegistryFunctions(
        list=registry_list_fn,
        start=registry_start_fn,
        stop=registry_stop_fn,
        invoke=registry_invoke_fn,
        tools=registry_tools_fn,
        details=registry_details_fn,
        health=registry_health_fn,
        discover=registry_discover_fn,
        discovered=registry_discovered_fn,
        quarantine=registry_quarantine_fn,
        approve=registry_approve_fn,
        sources=registry_sources_fn,
        metrics=registry_metrics_fn,
    )

    _compat_factory = MCPServerFactory(registry)
    logger.info("fastmcp_server_configured_via_deprecated_api")


def create_fastmcp_server():
    """DEPRECATED: Use MCPServerFactory.create_server() instead.

    Returns:
        Configured FastMCP server instance.

    Raises:
        RuntimeError: If setup_fastmcp_server() was not called first.
    """
    import warnings

    warnings.warn(
        "create_fastmcp_server() is deprecated. Use MCPServerFactory instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    if _compat_factory is None:
        raise RuntimeError(
            "setup_fastmcp_server() must be called before create_fastmcp_server(). "
            "Consider migrating to MCPServerFactory."
        )

    return _compat_factory.create_server()


def run_fastmcp_server():
    """DEPRECATED: Use MCPServerFactory.create_asgi_app() with uvicorn.

    Runs the FastMCP HTTP server. Blocks until shutdown.

    Raises:
        RuntimeError: If setup_fastmcp_server() was not called first.
    """
    import warnings

    warnings.warn(
        "run_fastmcp_server() is deprecated. Use MCPServerFactory instead.",
        DeprecationWarning,
        stacklevel=2,
    )

    import uvicorn

    from .metrics import init_metrics

    if _compat_factory is None:
        raise RuntimeError(
            "setup_fastmcp_server() must be called before run_fastmcp_server(). Consider migrating to MCPServerFactory."
        )

    logger.info(
        "fastmcp_http_server_starting",
        host=_compat_factory.config.host,
        port=_compat_factory.config.port,
        streamable_http_path=_compat_factory.config.streamable_http_path,
        metrics_path="/metrics",
    )

    init_metrics(version="1.0.0")
    app = _compat_factory.create_asgi_app()

    uvicorn.run(
        app,
        host=_compat_factory.config.host,
        port=_compat_factory.config.port,
        log_level="warning",
        access_log=False,
    )


# =============================================================================
# Module exports
# =============================================================================

__all__ = [
    # New API
    "MCPServerFactory",
    "MCPServerFactoryBuilder",
    "RegistryFunctions",
    "ServerConfig",
    # Protocols
    "RegistryListFn",
    "RegistryStartFn",
    "RegistryStopFn",
    "RegistryInvokeFn",
    "RegistryToolsFn",
    "RegistryDetailsFn",
    "RegistryHealthFn",
    "RegistryDiscoverFn",
    "RegistryDiscoveredFn",
    "RegistryQuarantineFn",
    "RegistryApproveFn",
    "RegistrySourcesFn",
    "RegistryMetricsFn",
    # Deprecated (backward compatibility)
    "setup_fastmcp_server",
    "create_fastmcp_server",
    "run_fastmcp_server",
]


if __name__ == "__main__":
    from .logging_config import setup_logging

    setup_logging(level="INFO", json_format=False)

    # Example with deprecated API (will emit warning)
    run_fastmcp_server()
