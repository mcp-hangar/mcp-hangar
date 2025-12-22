"""MCP HTTP Server using FastMCP.

This provides proper MCP over HTTP support using the official mcp library.
FastMCP handles SSE and Streamable HTTP transports automatically.

Endpoints (HTTP mode):
- /health : liveness (cheap ping)
- /ready  : readiness (checks internal registry wiring + basic runtime state)
- /metrics: prometheus metrics
"""

import logging

from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

# Global registry functions (will be set by setup)
_registry_list = None
_registry_start = None
_registry_stop = None
_registry_tools = None
_registry_invoke = None
_registry_details = None
_registry_health = None


def create_fastmcp_server():
    """Create FastMCP server with registry tools."""

    mcp = FastMCP(
        name="mcp-registry",
        host="0.0.0.0",
        port=8000,
        # Use streamable_http_path for LM Studio compatibility
        streamable_http_path="/mcp",
        sse_path="/sse",
        message_path="/messages/",
    )

    @mcp.tool()
    def registry_list(state_filter: str = None) -> dict:
        """List all providers with status and metadata.

        Args:
            state_filter: Optional filter by state (cold, ready, degraded, dead)
        """
        if _registry_list is None:
            return {"error": "Registry not initialized"}
        return _registry_list(state_filter=state_filter)

    @mcp.tool()
    def registry_start(provider: str) -> dict:
        """Explicitly start a provider and discover tools.

        Args:
            provider: Provider ID to start
        """
        if _registry_start is None:
            return {"error": "Registry not initialized"}
        return _registry_start(provider=provider)

    @mcp.tool()
    def registry_stop(provider: str) -> dict:
        """Stop a provider.

        Args:
            provider: Provider ID to stop
        """
        if _registry_stop is None:
            return {"error": "Registry not initialized"}
        return _registry_stop(provider=provider)

    @mcp.tool()
    def registry_invoke(provider: str, tool: str, arguments: dict, timeout: float = 30.0) -> dict:
        """Invoke a tool on a provider.

        Args:
            provider: Provider ID
            tool: Tool name to invoke
            arguments: Tool arguments as dictionary
            timeout: Timeout in seconds (default 30)
        """
        if _registry_invoke is None:
            return {"error": "Registry not initialized"}
        return _registry_invoke(provider=provider, tool=tool, arguments=arguments, timeout=timeout)

    @mcp.tool()
    def registry_tools(provider: str) -> dict:
        """Get detailed tool schemas for a provider.

        Args:
            provider: Provider ID
        """
        if _registry_tools is None:
            return {"error": "Registry not initialized"}
        return _registry_tools(provider=provider)

    @mcp.tool()
    def registry_details(provider: str) -> dict:
        """Get detailed information about a provider.

        Args:
            provider: Provider ID
        """
        if _registry_details is None:
            return {"error": "Registry not initialized"}
        return _registry_details(provider=provider)

    @mcp.tool()
    def registry_health() -> dict:
        """Get registry health status including provider counts and metrics."""
        if _registry_health is None:
            return {"error": "Registry not initialized"}
        return _registry_health()

    return mcp


def setup_fastmcp_server(
    registry_list_fn,
    registry_start_fn,
    registry_stop_fn,
    registry_tools_fn,
    registry_invoke_fn,
    registry_details_fn,
    registry_health_fn,
):
    """Setup FastMCP server with registry function references."""
    global _registry_list, _registry_start, _registry_stop, _registry_tools
    global _registry_invoke, _registry_details, _registry_health

    _registry_list = registry_list_fn
    _registry_start = registry_start_fn
    _registry_stop = registry_stop_fn
    _registry_tools = registry_tools_fn
    _registry_invoke = registry_invoke_fn
    _registry_details = registry_details_fn
    _registry_health = registry_health_fn

    logger.info("FastMCP server configured with registry functions")


def run_fastmcp_server():
    """Run the FastMCP HTTP server."""

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse, PlainTextResponse
    from starlette.routing import Route
    import uvicorn

    from .metrics import get_metrics, init_metrics, update_provider_state

    logger.info("Starting FastMCP HTTP server on 0.0.0.0:8000")
    print("🌐 MCP Registry FastMCP Server starting on http://0.0.0.0:8000", flush=True)
    print("📚 Streamable HTTP: http://0.0.0.0:8000/mcp", flush=True)
    print("📊 Metrics: http://0.0.0.0:8000/metrics", flush=True)

    # Initialize server metrics
    init_metrics(version="1.0.0")

    mcp = create_fastmcp_server()

    # Metrics endpoint
    async def metrics_endpoint(request):
        """Prometheus metrics endpoint."""
        # Update provider state metrics before returning
        if _registry_list:
            try:
                providers_data = _registry_list()
                if isinstance(providers_data, dict) and "providers" in providers_data:
                    for p in providers_data.get("providers", []):
                        pid = p.get("provider_id") or p.get("name") or p.get("id")
                        if not pid:  # Skip if no valid provider ID
                            continue
                        state = p.get("state", "cold")
                        mode = p.get("mode", "subprocess")
                        update_provider_state(pid, state, mode)
            except Exception as e:
                logger.debug(f"Could not update provider metrics: {e}")

        metrics_output = get_metrics()
        return PlainTextResponse(metrics_output, media_type="text/plain; version=0.0.4; charset=utf-8")

    # Health endpoint (liveness)
    async def health_endpoint(request):
        """Liveness endpoint (cheap ping)."""
        return JSONResponse({"status": "ok", "service": "mcp-registry"})

    # Readiness endpoint (internal checks)
    async def ready_endpoint(request):
        """
        Readiness endpoint.

        Meant for load balancers / orchestrators.
        Performs lightweight internal checks to confirm the app is ready to serve traffic.
        """
        checks = {}

        # 1) Registry wiring present (setup_fastmcp_server called)
        checks["registry_wired"] = all(
            fn is not None
            for fn in (
                _registry_list,
                _registry_start,
                _registry_stop,
                _registry_tools,
                _registry_invoke,
                _registry_details,
                _registry_health,
            )
        )

        # 2) Registry list callable returns a dict (base sanity)
        try:
            if _registry_list is None:
                raise RuntimeError("registry_list not configured")
            data = _registry_list()
            checks["registry_list_ok"] = isinstance(data, dict) and "providers" in data
        except Exception as e:
            checks["registry_list_ok"] = False
            checks["registry_list_error"] = str(e)

        # 3) Registry health callable returns a dict with status
        try:
            if _registry_health is None:
                raise RuntimeError("registry_health not configured")
            h = _registry_health()
            checks["registry_health_ok"] = isinstance(h, dict) and "status" in h
        except Exception as e:
            checks["registry_health_ok"] = False
            checks["registry_health_error"] = str(e)

        ready = (
            checks.get("registry_wired") is True
            and checks.get("registry_list_ok") is True
            and checks.get("registry_health_ok") is True
        )

        return JSONResponse(
            {
                "ready": ready,
                "service": "mcp-registry",
                "checks": checks,
            },
            status_code=200 if ready else 503,
        )

    # Create routes for metrics/health/readiness
    routes = [
        Route("/metrics", metrics_endpoint, methods=["GET"]),
        Route("/health", health_endpoint, methods=["GET"]),
        Route("/ready", ready_endpoint, methods=["GET"]),
    ]

    # Create a simple wrapper app that adds metrics/health routes
    # and delegates everything else to FastMCP
    metrics_app = Starlette(routes=routes)
    mcp_app = mcp.streamable_http_app()

    async def combined_app(scope, receive, send):
        """Combined ASGI app that routes to metrics or MCP."""
        if scope["type"] == "http":
            path = scope.get("path", "")
            if path == "/metrics" or path == "/health" or path == "/ready":
                await metrics_app(scope, receive, send)
                return
        await mcp_app(scope, receive, send)

    # Run with uvicorn
    uvicorn.run(combined_app, host="0.0.0.0", port=8000, log_level="info")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_fastmcp_server()
