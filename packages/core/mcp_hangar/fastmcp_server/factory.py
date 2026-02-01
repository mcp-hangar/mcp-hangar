"""MCPServerFactory for creating configured FastMCP servers.

The factory encapsulates all dependencies needed to create an MCP server,
enabling proper dependency injection and testability.
"""

from typing import Any, Optional, TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from starlette.applications import Starlette

from ..logging_config import get_logger
from .asgi import create_auth_combined_app, create_combined_asgi_app, create_health_routes
from .config import HangarFunctions, ServerConfig

if TYPE_CHECKING:
    from ..server.auth_bootstrap import AuthComponents
    from .builder import MCPServerFactoryBuilder

logger = get_logger(__name__)


class MCPServerFactory:
    """Factory for creating configured FastMCP servers.

    This factory encapsulates all dependencies needed to create an MCP server,
    enabling proper dependency injection and testability.

    Usage:
        # Direct instantiation
        factory = MCPServerFactory(hangar_functions)
        mcp = factory.create_server()
        app = factory.create_asgi_app()

        # With authentication (opt-in)
        factory = MCPServerFactory(
            hangar_functions,
            auth_components=auth_components,
            config=ServerConfig(auth_enabled=True),
        )
        app = factory.create_asgi_app()

        # Or use the builder pattern
        factory = (MCPServerFactory.builder()
            .with_hangar(list_fn, start_fn, ...)
            .with_discovery(discover_fn, ...)
            .with_auth(auth_components)
            .with_config(host="0.0.0.0", port=9000, auth_enabled=True)
            .build())
    """

    def __init__(
        self,
        hangar: HangarFunctions,
        config: ServerConfig | None = None,
        auth_components: Optional["AuthComponents"] = None,
    ):
        """Initialize factory with dependencies.

        Args:
            hangar: Control plane function implementations.
            config: Server configuration (uses defaults if None).
            auth_components: Optional auth components for authentication/authorization.
        """
        self._hangar = hangar
        self._config = config or ServerConfig()
        self._auth_components = auth_components
        self._mcp: FastMCP | None = None

    @classmethod
    def builder(cls) -> "MCPServerFactoryBuilder":
        """Create a builder for fluent configuration.

        Returns:
            MCPServerFactoryBuilder instance.
        """
        from .builder import MCPServerFactoryBuilder

        return MCPServerFactoryBuilder()

    @property
    def hangar(self) -> HangarFunctions:
        """Get the control plane functions."""
        return self._hangar

    @property
    def config(self) -> ServerConfig:
        """Get the server configuration."""
        return self._config

    def create_server(self) -> FastMCP:
        """Create and configure FastMCP server instance.

        The server is cached - repeated calls return the same instance.

        Returns:
            Configured FastMCP server with all tools registered.
        """
        if self._mcp is not None:
            return self._mcp

        mcp = FastMCP(
            name="mcp-hangar",
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
            discovery_enabled=self._hangar.discover is not None,
        )

        return mcp

    def create_asgi_app(self) -> Any:
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
        mcp = self.create_server()
        mcp_app = mcp.streamable_http_app()

        # Log if auth is configured
        if self._config.auth_enabled and self._auth_components:
            logger.info(
                "auth_middleware_enabled",
                skip_paths=self._config.auth_skip_paths,
                trusted_proxies=list(self._config.trusted_proxies),
            )

        # Create auxiliary routes
        routes = create_health_routes(
            run_readiness_checks=self._run_readiness_checks,
            update_metrics=self._update_metrics,
        )
        aux_app = Starlette(routes=routes)

        # Create auth-aware combined app
        if self._config.auth_enabled and self._auth_components:
            return create_auth_combined_app(aux_app, mcp_app, self._auth_components, self._config)
        else:
            return create_combined_asgi_app(aux_app, mcp_app)

    def _register_core_tools(self, mcp: FastMCP) -> None:
        """Register core control plane tools.

        Args:
            mcp: FastMCP server instance.
        """
        hgr = self._hangar

        @mcp.tool()
        def hangar_list(state_filter: str = None) -> dict:
            """List all managed providers with lifecycle state and metadata.

            Args:
                state_filter: Optional filter by state (cold, ready, degraded, dead)
            """
            return hgr.list(state_filter=state_filter)

        @mcp.tool()
        def hangar_start(provider: str) -> dict:
            """Explicitly start a provider and discover tools.

            Args:
                provider: Provider ID to start
            """
            return hgr.start(provider=provider)

        @mcp.tool()
        def hangar_stop(provider: str) -> dict:
            """Stop a provider.

            Args:
                provider: Provider ID to stop
            """
            return hgr.stop(provider=provider)

        @mcp.tool()
        def hangar_invoke(
            provider: str,
            tool: str,
            arguments: dict | None = None,
            timeout: float = 30.0,
        ) -> dict:
            """Invoke a tool on a provider.

            Args:
                provider: Provider ID
                tool: Tool name to invoke
                arguments: Tool arguments as dictionary (default: empty)
                timeout: Timeout in seconds (default 30)
            """
            return hgr.invoke(
                provider=provider,
                tool=tool,
                arguments=arguments or {},
                timeout=timeout,
            )

        @mcp.tool()
        def hangar_tools(provider: str) -> dict:
            """Get detailed tool schemas for a provider.

            Args:
                provider: Provider ID
            """
            return hgr.tools(provider=provider)

        @mcp.tool()
        def hangar_details(provider: str) -> dict:
            """Get detailed information about a provider.

            Args:
                provider: Provider ID
            """
            return hgr.details(provider=provider)

        @mcp.tool()
        def hangar_health() -> dict:
            """Get control plane health status including provider counts and metrics."""
            return hgr.health()

    def _register_discovery_tools(self, mcp: FastMCP) -> None:
        """Register discovery tools (if enabled).

        Args:
            mcp: FastMCP server instance.
        """
        hgr = self._hangar

        @mcp.tool()
        async def hangar_discover() -> dict:
            """Trigger immediate discovery cycle.

            Runs discovery across all configured sources and returns
            statistics about discovered, added, and quarantined providers.
            """
            if hgr.discover is None:
                return {"error": "Discovery not configured"}
            return await hgr.discover()

        @mcp.tool()
        def hangar_discovered() -> dict:
            """List all discovered providers pending addition.

            Shows providers found by discovery but not yet added,
            typically due to auto_register=false or pending approval.
            """
            if hgr.discovered is None:
                return {"error": "Discovery not configured"}
            return hgr.discovered()

        @mcp.tool()
        def hangar_quarantine() -> dict:
            """List quarantined providers with failure reasons.

            Shows providers that failed validation and are waiting
            for manual approval or rejection.
            """
            if hgr.quarantine is None:
                return {"error": "Discovery not configured"}
            return hgr.quarantine()

        @mcp.tool()
        async def hangar_approve(provider: str) -> dict:
            """Approve a quarantined provider for addition.

            Args:
                provider: Name of the quarantined provider to approve
            """
            if hgr.approve is None:
                return {"error": "Discovery not configured"}
            return await hgr.approve(provider=provider)

        @mcp.tool()
        def hangar_sources() -> dict:
            """List configured discovery sources with health status.

            Shows all discovery sources (kubernetes, docker, filesystem, entrypoint)
            with their current health and last discovery timestamp.
            """
            if hgr.sources is None:
                return {"error": "Discovery not configured"}
            return hgr.sources()

        @mcp.tool()
        def hangar_metrics(format: str = "summary") -> dict:
            """Get control plane metrics and statistics.

            Args:
                format: Output format - "summary" (default), "prometheus", or "detailed"

            Returns metrics including provider states, tool call counts, errors,
            discovery statistics, and performance data.
            """
            if hgr.metrics is None:
                return {"error": "Metrics not available"}
            return hgr.metrics(format=format)

    def _run_readiness_checks(self) -> dict[str, Any]:
        """Run readiness checks.

        Returns:
            Dictionary of check names to results.
        """
        checks: dict[str, Any] = {}

        # Check hangar wiring
        checks["hangar_wired"] = True

        # Check hangar list
        try:
            data = self._hangar.list()
            checks["hangar_list_ok"] = isinstance(data, dict) and "providers" in data
        except Exception as e:
            checks["hangar_list_ok"] = False
            checks["hangar_list_error"] = str(e)

        # Check hangar health
        try:
            h = self._hangar.health()
            checks["hangar_health_ok"] = isinstance(h, dict) and "status" in h
        except Exception as e:
            checks["hangar_health_ok"] = False
            checks["hangar_health_error"] = str(e)

        return checks

    def _update_metrics(self) -> None:
        """Update provider state metrics."""
        from ..metrics import update_provider_state

        try:
            data = self._hangar.list()
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


__all__ = [
    "MCPServerFactory",
]
