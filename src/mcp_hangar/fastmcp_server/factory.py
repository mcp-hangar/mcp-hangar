"""MCPServerFactory for creating configured FastMCP servers.

The factory encapsulates all dependencies needed to create an MCP server,
enabling proper dependency injection and testability.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

from mcp_hangar import __version__
from mcp_hangar._sdk_compat import FastMCP, new_mcp_server

from ..logging_config import get_logger
from .config import HANGAR_SERVER_NAME, HangarFunctions, ServerConfig
from .modern_surface import register_modern_surface

if TYPE_CHECKING:
    from ..domain.services.task_digest_guard import TaskDigestGuard
    from ..domain.services.task_ownership import TaskOwnershipRegistry

logger = get_logger(__name__)


class MCPServerFactory:
    """Factory for creating configured FastMCP servers.

    This factory encapsulates all dependencies needed to create an MCP server,
    enabling proper dependency injection and testability.

    Usage:
        # Direct instantiation
        factory = MCPServerFactory(hangar_functions)
        mcp = factory.create_server()

        # With authentication (opt-in)
        factory = MCPServerFactory(
            hangar_functions,
            auth_components=auth_components,
            config=ServerConfig(auth_enabled=True),
        )
    """

    def __init__(
        self,
        hangar: HangarFunctions,
        config: ServerConfig | None = None,
        auth_components: Any = None,
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
        # Shared registry binding MCP task handles to their owning
        # tenant/principal; populated when governed tasks are enabled.
        self._task_ownership_registry: TaskOwnershipRegistry | None = None
        # Shared guard binding MCP task handles to the tool digest pinned on the
        # invoke path; re-verified fail-closed on result retrieval (#320).
        self._task_digest_guard: TaskDigestGuard | None = None

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

        mcp = new_mcp_server(
            HANGAR_SERVER_NAME,
            # Explicit: unset, the SDK reports its own version as the server's,
            # disagreeing with the version server/discover reports (#560).
            version=__version__,
            host=self._config.host,
            port=self._config.port,
            streamable_http_path=self._config.streamable_http_path,
            sse_path=self._config.sse_path,
            message_path=self._config.message_path,
        )

        self._register_core_tools(mcp)
        self._register_discovery_tools(mcp)
        self._register_interceptors_list(mcp)
        self._register_server_discover(mcp)
        self._maybe_register_flat_tool_handlers(mcp)
        self._enable_governed_tasks(mcp)
        self._advertise_governance_extensions(mcp)
        self._advertise_tasks_capability(mcp)
        # Last, after every registration above, so it judges the final surface.
        self._withdraw_unserved_capabilities(mcp)

        self._mcp = mcp
        logger.info(
            "fastmcp_server_created",
            host=self._config.host,
            port=self._config.port,
            discovery_enabled=self._hangar.discover is not None,
        )

        return mcp

    def _register_core_tools(self, mcp: FastMCP) -> None:
        """Register core control plane tools.

        Args:
            mcp: FastMCP server instance.
        """
        hgr = self._hangar

        @mcp.tool()
        def hangar_list(state_filter: str | None = None) -> dict:
            """List all managed mcp_servers with lifecycle state and metadata.

            Args:
                state_filter: Optional filter by state (cold, ready, degraded, dead)
            """
            return hgr.list(state_filter=state_filter)

        @mcp.tool()
        def hangar_start(mcp_server: str) -> dict:
            """Explicitly start a mcp_server and discover tools.

            Args:
                mcp_server: McpServer ID to start
            """
            return hgr.start(mcp_server=mcp_server)

        @mcp.tool()
        def hangar_stop(mcp_server: str) -> dict:
            """Stop a mcp_server.

            Args:
                mcp_server: McpServer ID to stop
            """
            return hgr.stop(mcp_server=mcp_server)

        @mcp.tool()
        def hangar_invoke(
            mcp_server: str,
            tool: str,
            arguments: dict | None = None,
            timeout: float = 30.0,
        ) -> dict:
            """Invoke a tool on a mcp_server.

            Args:
                mcp_server: McpServer ID
                tool: Tool name to invoke
                arguments: Tool arguments as dictionary (default: empty)
                timeout: Timeout in seconds (default 30)
            """
            return hgr.invoke(
                mcp_server=mcp_server,
                tool=tool,
                arguments=arguments or {},
                timeout=timeout,
            )

        @mcp.tool()
        def hangar_tools(mcp_server: str) -> dict:
            """Get detailed tool schemas for a mcp_server.

            Args:
                mcp_server: McpServer ID
            """
            return hgr.tools(mcp_server=mcp_server)

        @mcp.tool()
        def hangar_details(mcp_server: str) -> dict:
            """Get detailed information about a mcp_server.

            Args:
                mcp_server: McpServer ID
            """
            return hgr.details(mcp_server=mcp_server)

        @mcp.tool()
        def hangar_health() -> dict:
            """Get control plane health status including mcp_server counts and metrics."""
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
            statistics about discovered, added, and quarantined mcp_servers.
            """
            if hgr.discover is None:
                return {"error": "Discovery not configured"}
            return await hgr.discover()

        @mcp.tool()
        def hangar_discovered() -> dict:
            """List all discovered mcp_servers pending addition.

            Shows mcp_servers found by discovery but not yet added,
            typically due to auto_register=false or pending approval.
            """
            if hgr.discovered is None:
                return {"error": "Discovery not configured"}
            return hgr.discovered()

        @mcp.tool()
        def hangar_quarantine() -> dict:
            """List quarantined mcp_servers with failure reasons.

            Shows mcp_servers that failed validation and are waiting
            for manual approval or rejection.
            """
            if hgr.quarantine is None:
                return {"error": "Discovery not configured"}
            return hgr.quarantine()

        @mcp.tool()
        async def hangar_approve(mcp_server: str) -> dict:
            """Approve a quarantined mcp_server for addition.

            Args:
                mcp_server: Name of the quarantined mcp_server to approve
            """
            if hgr.approve is None:
                return {"error": "Discovery not configured"}
            return await hgr.approve(mcp_server=mcp_server)

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

            Returns metrics including mcp_server states, tool call counts, errors,
            discovery statistics, and performance data.
            """
            if hgr.metrics is None:
                return {"error": "Metrics not available"}
            return hgr.metrics(format=format)

    def _enable_governed_tasks(self, mcp: FastMCP) -> None:
        """Wire the ADR-014 governed task-relay serving surface (Phase 2); dark by default.

        Delegates to the shared wiring so the factory path and the HTTP-serve
        bootstrap path activate the relay identically (see ``task_relay_wiring``).
        Gated on ``HAS_NATIVE_TASKS and config.relay_tasks_enabled``.
        """
        from .task_relay_wiring import enable_governed_task_relay

        enable_governed_task_relay(mcp, relay_tasks_enabled=self._config.relay_tasks_enabled)

    def _advertise_tasks_capability(self, mcp: FastMCP) -> None:
        """Advertise the first-class ``tasks`` server capability at INITIALIZE (ADR-014).

        Delegates to the shared wiring (see ``task_relay_wiring``); gated on the
        same static kill-switch as handler registration.
        """
        from .task_relay_wiring import advertise_tasks_capability

        advertise_tasks_capability(mcp, relay_tasks_enabled=self._config.relay_tasks_enabled)

    @staticmethod
    def _advertise_governance_extensions(mcp: FastMCP) -> None:
        """Advertise Hangar governance as SEP-2133 experimental extensions.

        Delegates to the shared wiring so this path and the HTTP-serve bootstrap
        advertise the same set (see ``governance_extensions``).
        """
        from .governance_extensions import advertise_governance_extensions

        advertise_governance_extensions(mcp)

    @staticmethod
    def _withdraw_unserved_capabilities(mcp: FastMCP) -> None:
        """Stop claiming ``prompts`` / ``resources`` while neither is served (#888).

        Delegates to the shared wiring so this path and the HTTP-serve bootstrap
        advertise the same set (see ``served_capabilities``).
        """
        from .served_capabilities import withdraw_unserved_capabilities

        withdraw_unserved_capabilities(mcp)

    @staticmethod
    def _register_interceptors_list(mcp: FastMCP) -> None:
        from .interceptors_list import register_interceptors_list

        register_interceptors_list(mcp)

    @staticmethod
    def _register_server_discover(mcp: FastMCP) -> None:
        """Register the SEP-2575 ``server/discover`` entry point (#290).

        Exposes the per-tenant projection surface as a stateless discover
        result, in addition to ``tools/list``. Tenant scoping and isolation
        are inherited from the projection read-model — this is a no-op in
        terms of enforcement, purely an alternate read entry point.

        Delegates to the shared wiring so this path and the HTTP-serve
        bootstrap path expose the same surface (see ``modern_surface``).
        """
        register_modern_surface(mcp)

    @staticmethod
    def _maybe_register_flat_tool_handlers(mcp: FastMCP) -> None:
        """Replace tools/list and tools/call handlers in front_door mode.

        Delegates to the shared, mode-gated entry point so this path and the
        HTTP-serve bootstrap decide identically (see ``flat_tool_projection``).
        """
        from .flat_tool_projection import maybe_register_flat_tool_handlers

        maybe_register_flat_tool_handlers(mcp)

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
            checks["hangar_list_ok"] = isinstance(data, dict) and "mcp_servers" in data
        except Exception as e:  # noqa: BLE001 -- fault-barrier: health check probe must return result not crash
            checks["hangar_list_ok"] = False
            checks["hangar_list_error"] = str(e)

        # Check hangar health
        try:
            h = self._hangar.health()
            checks["hangar_health_ok"] = isinstance(h, dict) and "status" in h
        except Exception as e:  # noqa: BLE001 -- fault-barrier: health check probe must return result not crash
            checks["hangar_health_ok"] = False
            checks["hangar_health_error"] = str(e)

        return checks

    def _update_metrics(self) -> None:
        """Update mcp_server state metrics."""
        from ..metrics import update_mcp_server_state

        try:
            data = self._hangar.list()
            if isinstance(data, dict) and "mcp_servers" in data:
                for p in data.get("mcp_servers", []):
                    pid = p.get("mcp_server_id") or p.get("name") or p.get("id")
                    if pid:
                        update_mcp_server_state(
                            pid,
                            p.get("state", "cold"),
                            p.get("mode", "subprocess"),
                        )
        except Exception as e:  # noqa: BLE001 -- fault-barrier: metrics update must not crash server
            logger.debug("metrics_update_failed", error=str(e))


__all__ = [
    "MCPServerFactory",
]
