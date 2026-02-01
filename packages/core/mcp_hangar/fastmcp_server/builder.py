"""Builder for MCPServerFactory with fluent API.

Provides a convenient way to construct an MCPServerFactory
with optional components.
"""

from typing import TYPE_CHECKING

from .config import HangarFunctions, ServerConfig
from .protocols import (
    HangarApproveFn,
    HangarDetailsFn,
    HangarDiscoveredFn,
    HangarDiscoverFn,
    HangarHealthFn,
    HangarInvokeFn,
    HangarListFn,
    HangarMetricsFn,
    HangarQuarantineFn,
    HangarSourcesFn,
    HangarStartFn,
    HangarStopFn,
    HangarToolsFn,
)

if TYPE_CHECKING:
    from ..server.auth_bootstrap import AuthComponents
    from .factory import MCPServerFactory


class MCPServerFactoryBuilder:
    """Builder for MCPServerFactory with fluent API.

    Provides a convenient way to construct an MCPServerFactory
    with optional components.

    Usage:
        factory = (MCPServerFactory.builder()
            .with_hangar(list_fn, start_fn, stop_fn, invoke_fn, tools_fn, details_fn, health_fn)
            .with_discovery(discover_fn=my_discover)
            .with_config(port=9000)
            .build())
    """

    def __init__(self):
        """Initialize builder with empty state."""
        self._list_fn: HangarListFn | None = None
        self._start_fn: HangarStartFn | None = None
        self._stop_fn: HangarStopFn | None = None
        self._invoke_fn: HangarInvokeFn | None = None
        self._tools_fn: HangarToolsFn | None = None
        self._details_fn: HangarDetailsFn | None = None
        self._health_fn: HangarHealthFn | None = None

        self._discover_fn: HangarDiscoverFn | None = None
        self._discovered_fn: HangarDiscoveredFn | None = None
        self._quarantine_fn: HangarQuarantineFn | None = None
        self._approve_fn: HangarApproveFn | None = None
        self._sources_fn: HangarSourcesFn | None = None
        self._metrics_fn: HangarMetricsFn | None = None

        self._config: ServerConfig | None = None
        self._auth_components: "AuthComponents | None" = None

    def with_hangar(
        self,
        list_fn: HangarListFn,
        start_fn: HangarStartFn,
        stop_fn: HangarStopFn,
        invoke_fn: HangarInvokeFn,
        tools_fn: HangarToolsFn,
        details_fn: HangarDetailsFn,
        health_fn: HangarHealthFn,
    ) -> "MCPServerFactoryBuilder":
        """Set core control plane functions.

        Args:
            list_fn: Function to list providers.
            start_fn: Function to start a provider.
            stop_fn: Function to stop a provider.
            invoke_fn: Function to invoke a tool.
            tools_fn: Function to get tool schemas.
            details_fn: Function to get provider details.
            health_fn: Function to get control plane health.

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
        discover_fn: HangarDiscoverFn | None = None,
        discovered_fn: HangarDiscoveredFn | None = None,
        quarantine_fn: HangarQuarantineFn | None = None,
        approve_fn: HangarApproveFn | None = None,
        sources_fn: HangarSourcesFn | None = None,
        metrics_fn: HangarMetricsFn | None = None,
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

    def build(self) -> "MCPServerFactory":
        """Build the factory.

        Returns:
            Configured MCPServerFactory instance.

        Raises:
            ValueError: If required hangar functions not provided.
        """
        from .factory import MCPServerFactory

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
            raise ValueError("All core hangar functions must be provided via with_hangar()")

        hangar = HangarFunctions(
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

        return MCPServerFactory(hangar, self._config, self._auth_components)


__all__ = [
    "MCPServerFactoryBuilder",
]
